from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest

from lawvm.new_zealand.effect_candidates import NZEffectCandidatePreflightReport

from lawvm.new_zealand.chain_replay import (
    SKIP_ALREADY_TOMBSTONED,
    SKIP_AMBIGUOUS_TARGET,
    SKIP_FUTURE,
    SKIP_TARGET_ABSENT,
    SKIP_UNEXTRACTABLE,
    SKIP_UNRESOLVED_TARGET,
    NZChainRepealOp,
    NZChainTransition,
    _apply_transition,
    _EvolvingTree,
    _similarity_point,
    _stable_path,
    build_archived_work_chain_replay,
    build_chain_replay,
    build_nz_repeal_chain,
)
from lawvm.new_zealand.source_tree import NZSourceDocument, NZSourceNode
from lawvm.new_zealand.version_diff import NZArchivedVersion
from lawvm.tools.cli import _build_parser


def _node(
    path: tuple[str, ...],
    *,
    text: str = "",
    heading: str = "",
    deletion: str = "",
    kind: str = "prov",
    source_zone: str = "body",
    xml_id: str = "",
) -> NZSourceNode:
    return NZSourceNode(
        kind=kind,
        path=path,
        xml_id=xml_id,
        xml_path="",
        source_zone=source_zone,
        label=path[-1].split(":", 1)[-1] if path else "",
        heading=heading,
        deletion_status=deletion,
        text=text,
        history=(),
    )


def _doc(nodes: tuple[NZSourceNode, ...], *, version_id: str = "v") -> NZSourceDocument:
    return NZSourceDocument(
        xml_locator=version_id,
        version_id=version_id,
        metadata={},
        nodes=nodes,
        document_history=(),
    )


def _op(
    row_id: str,
    date: str,
    source_path: tuple[str, ...] | None,
    *,
    amending: str = "amend_act",
    resolution: str = "exact_source_path",
) -> NZChainRepealOp:
    return NZChainRepealOp(
        row_id=row_id,
        amendment_date_iso=date,
        amending_work_id=amending,
        source_path=source_path,
        target_resolution_status=resolution,
    )


# --- Chain enumeration ordering ---


class _FakePreflightReport:
    """Stand-in for NZEffectCandidatePreflightReport for build_nz_repeal_chain.

    build_nz_repeal_chain consumes only what _replayable_repeal_rows reads. We
    therefore monkeypatch _replayable_repeal_rows in the enumeration test rather
    than building a full preflight; here we keep a tiny placeholder object.
    """


def test_build_nz_repeal_chain_orders_transitions_by_iso_date(monkeypatch: pytest.MonkeyPatch) -> None:
    import lawvm.new_zealand.chain_replay as mod

    class _Row:
        def __init__(self, row_id: str, date: str, amending: str, path: tuple[str, ...]):
            self.row_id = row_id
            self.amendment_date_iso = date
            self.amending_work_id = amending
            self.latest_oracle_target_resolution_status = "exact_source_path"
            # operation must yield a source path via _source_path_for_address.
            self._path = path
            self.operation = object()

    rows = (
        _Row("r3", "2014-05-01", "act_b", ("prov:3",)),
        _Row("r1", "2008-01-01", "act_a", ("prov:1",)),
        _Row("r2", "2008-01-01", "act_a", ("prov:2",)),
        _Row("r4", "2010-01-01", "act_c", ("prov:4",)),
    )
    path_by_op = {row.operation: row._path for row in rows}

    monkeypatch.setattr(mod, "_replayable_repeal_rows", lambda _pf: rows)
    monkeypatch.setattr(mod, "_source_path_for_address", lambda op: path_by_op[op])

    transitions = build_nz_repeal_chain(
        cast(NZEffectCandidatePreflightReport, _FakePreflightReport())
    )

    # Transitions are ISO-date ordered.
    assert [t.amendment_date_iso for t in transitions] == [
        "2008-01-01",
        "2010-01-01",
        "2014-05-01",
    ]
    # Within a date, ops are ordered by (amending_work_id, row_id).
    first = transitions[0]
    assert first.n_ops == 2
    assert [op.row_id for op in first.ops] == ["r1", "r2"]


# --- Sequential apply on the evolving tree ---


def test_apply_transition_tombstones_exact_target_on_evolving_tree() -> None:
    tree = _EvolvingTree(
        _doc((_node(("prov:1",), text="alpha"), _node(("prov:2",), text="beta")))
    )
    transition = NZChainTransition(
        amendment_date_iso="2010-01-01", ops=(_op("r1", "2010-01-01", ("prov:2",)),)
    )

    applied, skips, applied_paths = _apply_transition(
        tree, transition, latest_version_date="2024-01-01"
    )

    assert applied == 1
    assert skips == []
    assert applied_paths == [("prov:2",)]
    index = {n.path: n for n in tree.document.nodes}
    assert index[("prov:2",)].deletion_status  # tombstoned
    assert not index[("prov:1",)].deletion_status  # untouched neighbour


def test_apply_transition_carries_tree_forward_across_transitions() -> None:
    # Two transitions on one evolving tree: the second sees the first's mutation.
    tree = _EvolvingTree(
        _doc((_node(("prov:1",), text="a"), _node(("prov:2",), text="b"), _node(("prov:3",), text="c")))
    )
    t1 = NZChainTransition("2010-01-01", (_op("r1", "2010-01-01", ("prov:1",)),))
    t2 = NZChainTransition("2011-01-01", (_op("r2", "2011-01-01", ("prov:3",)),))

    _apply_transition(tree, t1, latest_version_date="2024-01-01")
    _apply_transition(tree, t2, latest_version_date="2024-01-01")

    index = {n.path: n for n in tree.document.nodes}
    assert index[("prov:1",)].deletion_status
    assert not index[("prov:2",)].deletion_status
    assert index[("prov:3",)].deletion_status


# --- Typed skip buckets (never a silent drop) ---


def test_apply_transition_types_every_unapplicable_op() -> None:
    tree = _EvolvingTree(
        _doc(
            (
                _node(("prov:keep",), text="x"),
                _node(("prov:dead",), text="y", deletion="repealed"),
                # Two nodes at the same path -> ambiguous.
                _node(("prov:dup",), text="p", xml_id="A"),
                _node(("prov:dup",), text="q", xml_id="B"),
            )
        )
    )
    ops = (
        _op("future", "2099-01-01", ("prov:keep",)),  # future skip
        _op("noextract", "2010-01-01", None),  # unextractable skip
        _op("unresolved", "2010-01-01", ("prov:keep",), resolution="not_exact"),  # unresolved skip
        _op("absent", "2010-01-01", ("prov:missing",)),  # target absent skip
        _op("dead", "2010-01-01", ("prov:dead",)),  # already tombstoned skip
        _op("dup", "2010-01-01", ("prov:dup",)),  # ambiguous skip
    )
    transition = NZChainTransition("2010-01-01", ops)

    applied, skips, applied_paths = _apply_transition(
        tree, transition, latest_version_date="2024-01-01"
    )

    assert applied == 0
    assert applied_paths == []
    buckets = {skip.row_id: skip.bucket for skip in skips}
    assert buckets == {
        "future": SKIP_FUTURE,
        "noextract": SKIP_UNEXTRACTABLE,
        "unresolved": SKIP_UNRESOLVED_TARGET,
        "absent": SKIP_TARGET_ABSENT,
        "dead": SKIP_ALREADY_TOMBSTONED,
        "dup": SKIP_AMBIGUOUS_TARGET,
    }
    # Replayed + skipped = full census: nothing dropped silently.
    assert len(skips) == len(ops)


# --- Similarity computed vs oracle ---


def test_similarity_point_high_when_trees_match() -> None:
    nodes = (_node(("prov:1",), text="same body"), _node(("prov:2",), text="other body"))
    replayed = _doc(nodes, version_id="r")
    oracle = _doc(nodes, version_id="o")
    version = NZArchivedVersion(version_id="o", xml_locator="o", version_date="2010-01-01")

    point = _similarity_point(
        replayed,
        oracle,
        version,
        transitions_applied=0,
        repeals_applied=0,
        repeals_skipped=0,
    )

    assert point.combined_similarity == pytest.approx(1.0)
    assert point.path_jaccard == pytest.approx(1.0)


def test_similarity_point_penalizes_missing_and_changed_paths() -> None:
    replayed = _doc((_node(("prov:1",), text="alpha"),), version_id="r")
    oracle = _doc(
        (_node(("prov:1",), text="totally different"), _node(("prov:2",), text="extra")),
        version_id="o",
    )
    version = NZArchivedVersion(version_id="o", xml_locator="o", version_date="2010-01-01")

    point = _similarity_point(
        replayed,
        oracle,
        version,
        transitions_applied=1,
        repeals_applied=1,
        repeals_skipped=2,
    )

    # Union has 2 paths; one is oracle-only (scores 0), the shared one differs.
    assert point.path_jaccard == pytest.approx(0.5)
    assert 0.0 < point.combined_similarity < 1.0
    # Carried coverage counters are reported alongside the score.
    assert point.repeals_applied_so_far == 1
    assert point.repeals_skipped_so_far == 2


def test_stable_path_collapses_positional_and_identity_segments() -> None:
    assert _stable_path(("part:5", "prov:147", "subprov#1525")) == ("part:5", "prov:147", "subprov")
    assert _stable_path(("part:5", "prov@X9", "subprov:1")) == ("part:5", "prov", "subprov:1")
    # A fully-labelled path is unchanged.
    assert _stable_path(("part:5", "prov:147", "subprov:1")) == ("part:5", "prov:147", "subprov:1")


def test_stable_track_ignores_id_churn_that_raw_track_penalizes() -> None:
    # Same logical node under churned anonymous ids: raw jaccard drops, stable
    # jaccard stays 1.0 and stable combined recovers.
    replayed = _doc((_node(("prov:1", "subprov#10"), text="body text"),), version_id="r")
    oracle = _doc((_node(("prov:1", "subprov#11"), text="body text"),), version_id="o")
    version = NZArchivedVersion(version_id="o", xml_locator="o", version_date="2010-01-01")

    point = _similarity_point(
        replayed, oracle, version, transitions_applied=0, repeals_applied=0, repeals_skipped=0
    )

    assert point.path_jaccard == pytest.approx(0.0)  # raw: #10 != #11
    assert point.path_jaccard_stable == pytest.approx(1.0)  # stable: both -> subprov
    assert point.combined_similarity_stable == pytest.approx(1.0)


# --- A small fixture chain end-to-end via build_chain_replay ---


class _FakeArchive:
    """Minimal archive exposing only the locator enumeration shape.

    The version inventory comes from ``locators`` + ``get`` (version detail
    JSON). The XML bytes are placeholders; the actual parsed documents are wired
    in via a monkeypatched ``_parse_archived_version`` keyed by version_id, so the
    fixture controls the parsed tree directly without round-tripping XML.
    """

    def __init__(self, work_id: str, dates: list[str]):
        self._work_id = work_id
        self._dates = dates

    def locators(self, pattern: str) -> list[str]:
        base = f"https://api.legislation.govt.nz/v0/versions/{self._work_id}_en_"
        return [f"{base}{date}/" for date in self._dates]

    def get(self, locator: str) -> bytes | None:
        import json

        for date in self._dates:
            version_id = f"{self._work_id}_en_{date}"
            if locator == f"https://api.legislation.govt.nz/v0/versions/{version_id}/":
                return json.dumps(
                    {"formats": [{"type": "xml", "url": f"xml://{version_id}"}]}
                ).encode("utf-8")
            if locator == f"xml://{version_id}":
                return b"<placeholder/>"
        return None


def test_build_chain_replay_over_fixture_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    import lawvm.new_zealand.chain_replay as mod

    work_id = "act_test_1"
    # V0 base: three live provisions. Oracle V1: prov:2 repealed (tombstoned),
    # prov:3 changed (a non-repeal change we will NOT apply -> source-honest gap).
    base = _doc(
        (_node(("prov:1",), text="a"), _node(("prov:2",), text="b"), _node(("prov:3",), text="c")),
        version_id="act_test_1_en_2010-01-01",
    )
    oracle_v1 = _doc(
        (
            _node(("prov:1",), text="a"),
            _node(("prov:2",), text="b", deletion="repealed"),
            _node(("prov:3",), text="c CHANGED by some other family"),
        ),
        version_id="act_test_1_en_2011-01-01",
    )
    archive = _FakeArchive(work_id, ["2010-01-01", "2011-01-01"])
    docs_by_version = {base.version_id: base, oracle_v1.version_id: oracle_v1}
    monkeypatch.setattr(
        mod,
        "_parse_archived_version",
        lambda _archive, version, _cache: docs_by_version[version.version_id],
    )

    # One authorized repeal on prov:2 effective on the V1 date.
    transition = NZChainTransition("2011-01-01", (_op("r1", "2011-01-01", ("prov:2",)),))
    monkeypatch.setattr(mod, "build_nz_repeal_chain", lambda _pf: (transition,))

    report = build_chain_replay(
        archive,
        work_id=work_id,
        preflight=cast(NZEffectCandidatePreflightReport, object()),
    )

    assert report.base_version_date == "2010-01-01"
    assert report.n_archived_versions == 2
    assert report.repeals_applied == 1
    assert report.repeals_skipped == 0
    # The repeal direction agrees with the oracle tombstone.
    assert report.oracle_tombstone_agreements == 1
    assert report.oracle_tombstone_disagreements == 0
    # Curve has one point per archived version, both high (only prov:3 differs).
    assert len(report.similarity_curve) == 2
    assert report.similarity_curve[0].combined_similarity == pytest.approx(1.0)
    assert 0.0 < report.similarity_curve[-1].combined_similarity < 1.0
    # Honesty flags hold.
    assert report.replay_claims is False
    assert report.to_jsonable()["report_kind"] == "experimental_dry_run_chain_replay"


# --- CLI wiring ---


def test_cli_exposes_nz_corpus_replay_chain() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["nz-corpus", "replay-chain", "--work-id", "act_public_1989_157", "--json"]
    )
    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "replay-chain"
    assert args.work_id == "act_public_1989_157"
    assert args.json is True


# --- Real-archive regression on the canary ---


_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_chain_replay_canary_act_public_1989_157() -> None:
    report = build_archived_work_chain_replay(_REAL_DB, "act_public_1989_157")
    summary = report.summary()

    # The canary has a real 33-version chain.
    assert summary["n_archived_versions"] == 33
    assert summary["base_version_date"] == "2007-09-03"
    assert summary["n_transitions"] >= 1

    # Replayed + skipped = full enumerated repeal-op census (nothing dropped).
    assert summary["repeals_applied"] + summary["repeals_skipped"] == summary["total_repeal_ops"]
    assert summary["repeals_applied"] > 0

    # Repeal direction is overwhelmingly correct against the oracle (the family
    # that works); a low number here would be a real replay-direction regression.
    agree = summary["oracle_tombstone_agreements"]
    disagree = summary["oracle_tombstone_disagreements"]
    assert agree + disagree == summary["repeals_applied"]
    assert agree / (agree + disagree) >= 0.95

    # Surviving-node text similarity stays high; the union/whole-tree combined is
    # lower because repeal-only cannot reproduce skipped non-repeal structure.
    final = report.final_similarity()
    assert final is not None
    assert final.shared_mean_similarity >= 0.80
    assert final.combined_similarity_stable >= final.combined_similarity

    # The curve starts at a perfect base (no transition applied yet at V0) and
    # degrades as skipped non-repeal changes accumulate down the chain.
    assert report.similarity_curve[0].combined_similarity == pytest.approx(1.0)
    assert report.similarity_curve[-1].combined_similarity < report.similarity_curve[0].combined_similarity

    # Honesty: experimental, no replay claims.
    assert report.replay_claims is False
    assert report.skip_bucket_counts()  # typed buckets present
