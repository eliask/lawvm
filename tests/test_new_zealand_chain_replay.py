from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest

from lawvm.new_zealand.effect_candidates import NZEffectCandidatePreflightReport
from lawvm.new_zealand import chain_replay as chain_replay_module

from lawvm.new_zealand.chain_replay import (
    CHAIN_FAMILY_ORDER,
    SKIP_ALREADY_TOMBSTONED,
    SKIP_AMBIGUOUS_TARGET,
    SKIP_AMENDING_UNRESOLVED,
    SKIP_FUTURE,
    SKIP_INSERT_ALREADY_PRESENT,
    SKIP_TARGET_ABSENT,
    SKIP_TEXT_OCCURRENCE_MISMATCH,
    SKIP_UNEXTRACTABLE,
    SKIP_UNRESOLVED_TARGET,
    NZChainOp,
    NZChainRepealOp,
    NZChainTransition,
    _apply_transition,
    _EvolvingTree,
    _similarity_point,
    _stable_path,
    build_archived_work_chain_replay,
    build_chain_replay,
    build_nz_chain,
    build_nz_repeal_chain,
    resolve_families,
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
    family: str = "repeal",
    amending: str = "amend_act",
    resolution: str = "exact_source_path",
    operation: object = None,
    amending_provision_href: str = "",
) -> NZChainRepealOp:
    return NZChainRepealOp(
        family=family,
        row_id=row_id,
        amendment_date_iso=date,
        amending_work_id=amending,
        source_path=source_path,
        target_resolution_status=resolution,
        operation=operation,
        amending_provision_href=amending_provision_href,
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

    applied, skips, applied_ops = _apply_transition(
        tree, transition, latest_version_date="2024-01-01"
    )

    assert applied == 1
    assert skips == []
    assert [op.target_path for op in applied_ops] == [("prov:2",)]
    assert [op.family for op in applied_ops] == ["repeal"]
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

    applied, skips, applied_ops = _apply_transition(
        tree, transition, latest_version_date="2024-01-01"
    )

    assert applied == 0
    assert applied_ops == []
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


def test_similarity_point_reuses_cleaned_text_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = (_node(("prov:1",), text="same body"), _node(("prov:2",), text="same body"))
    replayed = _doc(nodes, version_id="r")
    oracle = _doc(nodes, version_id="o")
    version = NZArchivedVersion(version_id="o", xml_locator="o", version_date="2010-01-01")
    calls = 0

    def clean_once(text: str) -> str:
        nonlocal calls
        calls += 1
        return text

    monkeypatch.setattr(chain_replay_module, "clean_similarity_text", clean_once)
    cache: dict[tuple[str, str, str], str] = {}

    first = _similarity_point(
        replayed,
        oracle,
        version,
        transitions_applied=0,
        repeals_applied=0,
        repeals_skipped=0,
        cleaned_text_cache=cache,
    )
    second = _similarity_point(
        replayed,
        oracle,
        version,
        transitions_applied=0,
        repeals_applied=0,
        repeals_skipped=0,
        cleaned_text_cache=cache,
    )

    assert first.combined_similarity == second.combined_similarity
    assert calls == 1


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
    monkeypatch.setattr(mod, "build_nz_chain", lambda _pf, _surface, families=None: (transition,))

    report = build_chain_replay(
        archive,
        work_id=work_id,
        preflight=cast(NZEffectCandidatePreflightReport, object()),
        families="repeal",
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
    # Default families is all (the all-families chain replay).
    assert args.families == "all"


def test_cli_replay_chain_families_flag() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "nz-corpus",
            "replay-chain",
            "--work-id",
            "act_public_1989_157",
            "--families",
            "repeal",
        ]
    )
    assert args.families == "repeal"


# --- Real-archive regression on the canary ---


_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.slow
def test_chain_replay_repeal_only_canary_act_public_1989_157() -> None:
    report = build_archived_work_chain_replay(
        _REAL_DB, "act_public_1989_157", families="repeal"
    )
    summary = report.summary()

    # The canary has a real 33-version chain.
    assert summary["n_archived_versions"] == 33
    assert summary["base_version_date"] == "2007-09-03"
    assert summary["n_transitions"] >= 1
    assert summary["families_requested"] == ["repeal"]

    # Applied + skipped = full enumerated repeal-op census (nothing dropped).
    assert summary["repeals_applied"] + summary["repeals_skipped"] == summary["total_repeal_ops"]
    assert summary["repeals_applied"] > 0

    # Repeal direction is overwhelmingly correct against the oracle (the family
    # that works); a low number here would be a real replay-direction regression.
    # In repeal-only mode every applied op is a repeal, so this equals applied.
    agree = summary["oracle_tombstone_agreements"]
    disagree = summary["oracle_tombstone_disagreements"]
    assert agree + disagree == summary["repeals_applied"]
    assert agree / (agree + disagree) >= 0.95

    final = report.final_similarity()
    assert final is not None
    assert final.shared_mean_similarity >= 0.80
    assert final.combined_similarity_stable >= final.combined_similarity

    # Repeal-only: no transition applies at the base version (the earliest repeal
    # is later than the base date), so the curve starts at a perfect base and
    # degrades as skipped non-repeal structure accumulates down the chain.
    assert report.similarity_curve[0].combined_similarity == pytest.approx(1.0)
    assert report.similarity_curve[-1].combined_similarity < report.similarity_curve[0].combined_similarity


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.slow
def test_chain_replay_all_families_beats_repeal_only_canary() -> None:
    repeal_only = build_archived_work_chain_replay(
        _REAL_DB, "act_public_1989_157", families="repeal"
    )
    all_families = build_archived_work_chain_replay(
        _REAL_DB, "act_public_1989_157", families="all"
    )

    repeal_final = repeal_only.final_similarity()
    all_final = all_families.final_similarity()
    assert repeal_final is not None and all_final is not None

    # The headline deliverable: folding all four families onto the single evolving
    # tree must RAISE the stable combined similarity vs the repeal-only baseline,
    # because genuinely-more-correct ops are applied (not loosened comparison).
    assert all_final.combined_similarity_stable > repeal_final.combined_similarity_stable
    assert all_final.combined_similarity > repeal_final.combined_similarity

    # All four families are enumerated and the non-repeal families apply > 0 ops
    # with high per-op oracle agreement (the lift is from correct ops).
    stats = {stat.family: stat for stat in all_families.per_family_stats}
    assert set(stats) == {"repeal", "text_replace", "replace", "insert"}
    for family in ("text_replace", "replace", "insert"):
        stat = stats[family]
        assert stat.applied > 0, f"{family} applied nothing"
        oracle_total = stat.oracle_agreements + stat.oracle_disagreements
        assert oracle_total == stat.applied
        assert stat.oracle_agreements / oracle_total >= 0.75

    # Honesty: applied + skipped = the full enumerated census per family (no silent
    # drops anywhere across families).
    for stat in all_families.per_family_stats:
        assert stat.applied + stat.skipped == stat.enumerated

    # Honesty: experimental, no replay claims.
    assert all_families.replay_claims is False
    assert all_families.skip_bucket_counts()  # typed buckets present


# --- All-families: per-op kernels applied on the evolving tree ---


class _FakeTextSelector:
    def __init__(self, match_text: str, occurrence: int = 1) -> None:
        self.match_text = match_text
        self.occurrence = occurrence


class _FakeTextPatch:
    def __init__(self, match_text: str, replacement: str | None, occurrence: int = 1) -> None:
        self.selector = _FakeTextSelector(match_text, occurrence)
        self.replacement = replacement


class _FakeTextOp:
    def __init__(self, match_text: str, replacement: str | None, occurrence: int = 1) -> None:
        self.text_patch = _FakeTextPatch(match_text, replacement, occurrence)


def _text_op(
    row_id: str,
    date: str,
    source_path: tuple[str, ...],
    *,
    match_text: str,
    replacement: str | None,
    occurrence: int = 1,
) -> NZChainOp:
    return _op(
        row_id,
        date,
        source_path,
        family="text_replace",
        operation=_FakeTextOp(match_text, replacement, occurrence),
    )


def _struct_payload(root_path: tuple[str, ...], *, root_text: str, descendants=()):
    """A minimal NZStructuralReplacement-like payload for swap/insert kernels.

    The kernels read only ``payload.root`` and ``payload.descendants``; the chain
    rebases their paths onto the resolved target. A tiny stand-in keeps the test
    independent of the amend-XML extractor.
    """

    from lawvm.new_zealand.source_tree import NZStructuralReplacement

    root = _node(root_path, text=root_text)
    return NZStructuralReplacement(root=root, descendants=tuple(descendants))


def test_text_replace_op_substitutes_single_occurrence_on_evolving_tree() -> None:
    tree = _EvolvingTree(_doc((_node(("prov:1",), text="the quick brown fox"),)))
    transition = NZChainTransition(
        "2010-01-01",
        (_text_op("t1", "2010-01-01", ("prov:1",), match_text="quick", replacement="slow"),),
    )

    applied, skips, applied_ops = _apply_transition(
        tree, transition, latest_version_date="2024-01-01"
    )

    assert applied == 1
    assert skips == []
    assert applied_ops[0].family == "text_replace"
    index = {n.path: n for n in tree.document.nodes}
    assert index[("prov:1",)].text == "the slow brown fox"


def test_text_replace_op_skips_when_old_text_not_single_occurrence() -> None:
    tree = _EvolvingTree(_doc((_node(("prov:1",), text="ab ab ab"),)))
    transition = NZChainTransition(
        "2010-01-01",
        (_text_op("t1", "2010-01-01", ("prov:1",), match_text="ab", replacement="cd"),),
    )

    applied, skips, _applied_ops = _apply_transition(
        tree, transition, latest_version_date="2024-01-01"
    )

    assert applied == 0
    assert [s.bucket for s in skips] == [SKIP_TEXT_OCCURRENCE_MISMATCH]
    assert skips[0].family == "text_replace"
    # The tree is untouched (a wrong op never mutates).
    assert {n.path: n.text for n in tree.document.nodes}[("prov:1",)] == "ab ab ab"


def test_evolving_tree_swap_subtree_replaces_node_and_descendants() -> None:
    tree = _EvolvingTree(
        _doc(
            (
                _node(("prov:1",), text="old root"),
                _node(("prov:1", "subprov:1"), text="old child"),
                _node(("prov:2",), text="neighbour"),
            )
        )
    )
    payload = _struct_payload(
        ("amend",),
        root_text="new root",
        descendants=(_node(("amend", "subprov:1"), text="new child"),),
    )

    tree.swap_subtree(("prov:1",), payload.root, payload.descendants)

    by_path = {n.path: n.text for n in tree.document.nodes}
    assert by_path[("prov:1",)] == "new root"
    assert by_path[("prov:1", "subprov:1")] == "new child"  # descendant rebased
    assert by_path[("prov:2",)] == "neighbour"  # neighbour untouched
    # Document order preserved: the swapped subtree sits where the target was.
    paths = [n.path for n in tree.document.nodes]
    assert paths == [("prov:1",), ("prov:1", "subprov:1"), ("prov:2",)]


def test_evolving_tree_insert_node_after_anchor_keeps_neighbours() -> None:
    tree = _EvolvingTree(
        _doc(
            (
                _node(("prov:18",), text="anchor body"),
                _node(("prov:18", "subprov:1"), text="anchor child"),
                _node(("prov:19",), text="next"),
            )
        )
    )
    payload = _struct_payload(("amend",), root_text="brand new 18A")

    tree.insert_node(
        ("prov:18",),
        "after",
        ("prov:18A",),
        payload.root,
        payload.descendants,
    )

    paths = [n.path for n in tree.document.nodes]
    # The new node lands after the anchor's WHOLE subtree, before prov:19.
    assert paths == [
        ("prov:18",),
        ("prov:18", "subprov:1"),
        ("prov:18A",),
        ("prov:19",),
    ]
    by_path = {n.path: n.text for n in tree.document.nodes}
    assert by_path[("prov:18A",)] == "brand new 18A"
    assert by_path[("prov:18",)] == "anchor body"  # anchor unchanged


def test_evolving_tree_insert_node_before_anchor() -> None:
    tree = _EvolvingTree(
        _doc((_node(("prov:a",), text="a body"), _node(("prov:b",), text="b body")))
    )
    payload = _struct_payload(("amend",), root_text="inserted before b")

    tree.insert_node(("prov:b",), "before", ("prov:aa",), payload.root, payload.descendants)

    paths = [n.path for n in tree.document.nodes]
    assert paths == [("prov:a",), ("prov:aa",), ("prov:b",)]


def test_cross_family_carry_forward_compose_on_one_tree() -> None:
    # repeal -> text_replace -> insert composed on one evolving tree; each op sees
    # the prior op's mutation.
    tree = _EvolvingTree(
        _doc(
            (
                _node(("prov:1",), text="repeal me"),
                _node(("prov:2",), text="alpha beta"),
            )
        )
    )
    t1 = NZChainTransition("2010-01-01", (_op("r1", "2010-01-01", ("prov:1",)),))
    t2 = NZChainTransition(
        "2011-01-01",
        (_text_op("t2", "2011-01-01", ("prov:2",), match_text="beta", replacement="gamma"),),
    )

    _apply_transition(tree, t1, latest_version_date="2024-01-01")
    _apply_transition(tree, t2, latest_version_date="2024-01-01")

    by_path = {n.path: n for n in tree.document.nodes}
    assert by_path[("prov:1",)].deletion_status  # repealed
    assert by_path[("prov:2",)].text == "alpha gamma"  # text substituted after


def test_structural_op_skips_when_archive_absent() -> None:
    # replace/insert require the amending act XML; with archive=None the op is a
    # typed amending-unresolved skip, never a silent drop and never a crash.
    tree = _EvolvingTree(_doc((_node(("prov:1",), text="x"),)))
    transition = NZChainTransition(
        "2010-01-01",
        (
            _op(
                "rep1",
                "2010-01-01",
                ("prov:1",),
                family="replace",
                amending_provision_href="p1",
            ),
        ),
    )

    applied, skips, _applied = _apply_transition(
        tree, transition, latest_version_date="2024-01-01", archive=None
    )

    assert applied == 0
    assert [s.bucket for s in skips] == [SKIP_AMENDING_UNRESOLVED]
    assert skips[0].family == "replace"


def test_insert_op_skips_when_target_already_present() -> None:
    tree = _EvolvingTree(_doc((_node(("prov:18A",), text="already here"),)))
    transition = NZChainTransition(
        "2010-01-01",
        (
            _op(
                "ins1",
                "2010-01-01",
                ("prov:18A",),
                family="insert",
                amending_provision_href="p1",
            ),
        ),
    )

    # A non-None archive sentinel: the already-present check runs before any XML.
    applied, skips, _applied = _apply_transition(
        tree, transition, latest_version_date="2024-01-01", archive=object()
    )

    assert applied == 0
    assert [s.bucket for s in skips] == [SKIP_INSERT_ALREADY_PRESENT]


# --- families flag + enumeration filtering ---


def test_resolve_families_spec() -> None:
    assert resolve_families(None) == frozenset(CHAIN_FAMILY_ORDER)
    assert resolve_families("all") == frozenset(CHAIN_FAMILY_ORDER)
    assert resolve_families("repeal") == frozenset({"repeal"})
    assert resolve_families("repeal,insert") == frozenset({"repeal", "insert"})
    with pytest.raises(ValueError):
        resolve_families("bogus")


def test_build_nz_chain_orders_families_within_a_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    import lawvm.new_zealand.chain_replay as mod

    # All four families effective on the SAME date must be emitted in
    # CHAIN_FAMILY_ORDER so the documented within-transition apply order holds.
    monkeypatch.setattr(
        mod,
        "_enumerate_repeal_ops",
        lambda _pf: [_op("rep", "2010-01-01", ("prov:9",))],
    )
    monkeypatch.setattr(
        mod,
        "_enumerate_text_replace_ops",
        lambda _pf: [_op("txt", "2010-01-01", ("prov:9",), family="text_replace")],
    )

    class _Surface:
        rows: tuple = ()

    def _fake_struct(_surface, family):
        return [_op(family, "2010-01-01", ("prov:9",), family=family)]

    monkeypatch.setattr(mod, "_enumerate_structural_ops", _fake_struct)

    transitions = build_nz_chain(
        cast(NZEffectCandidatePreflightReport, object()),
        _Surface(),
        families=frozenset(CHAIN_FAMILY_ORDER),
    )

    assert len(transitions) == 1
    assert [op.family for op in transitions[0].ops] == list(CHAIN_FAMILY_ORDER)


def test_build_nz_chain_respects_family_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    import lawvm.new_zealand.chain_replay as mod

    monkeypatch.setattr(
        mod, "_enumerate_repeal_ops", lambda _pf: [_op("rep", "2010-01-01", ("prov:1",))]
    )
    monkeypatch.setattr(
        mod,
        "_enumerate_text_replace_ops",
        lambda _pf: [_op("txt", "2010-01-01", ("prov:2",), family="text_replace")],
    )

    transitions = build_nz_chain(
        cast(NZEffectCandidatePreflightReport, object()),
        None,
        families=frozenset({"repeal"}),
    )

    families = {op.family for t in transitions for op in t.ops}
    assert families == {"repeal"}


# --- Multi-family fixture transition + divergence detection ---


def test_build_chain_replay_multi_family_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    import lawvm.new_zealand.chain_replay as mod

    work_id = "act_test_mf"
    base = _doc(
        (
            _node(("prov:1",), text="repeal me"),
            _node(("prov:2",), text="alpha beta"),
            _node(("prov:3",), text="old body"),
            _node(("prov:18",), text="anchor"),
        ),
        version_id="act_test_mf_en_2010-01-01",
    )
    # Oracle V1 reflects: prov:1 repealed, prov:2 text substituted, prov:3 replaced,
    # prov:18A inserted after prov:18.
    oracle_v1 = _doc(
        (
            _node(("prov:1",), text="repeal me", deletion="repealed"),
            _node(("prov:2",), text="alpha gamma"),
            _node(("prov:3",), text="new body"),
            _node(("prov:18",), text="anchor"),
            _node(("prov:18A",), text="inserted body"),
        ),
        version_id="act_test_mf_en_2011-01-01",
    )
    archive = _FakeArchive(work_id, ["2010-01-01", "2011-01-01"])
    docs_by_version = {base.version_id: base, oracle_v1.version_id: oracle_v1}
    monkeypatch.setattr(
        mod,
        "_parse_archived_version",
        lambda _archive, version, _cache: docs_by_version[version.version_id],
    )
    # Stub the amend-XML payload extractors so the structural kernels run without
    # real XML (the chain rebases these onto the resolved/anchored paths).
    monkeypatch.setattr(
        mod,
        "_extract_replacement_payload",
        lambda _op, _arch, _cache, **_kw: _struct_payload(("amend",), root_text="new body"),
    )
    monkeypatch.setattr(
        mod,
        "_extract_insertion_payload",
        lambda _op, _k, _l, _arch, _cache, **_kw: _struct_payload(("amend",), root_text="inserted body"),
    )

    transition = NZChainTransition(
        "2011-01-01",
        (
            _op("rep", "2011-01-01", ("prov:1",), family="repeal"),
            _text_op("txt", "2011-01-01", ("prov:2",), match_text="beta", replacement="gamma"),
            _op("rpl", "2011-01-01", ("prov:3",), family="replace", amending_provision_href="h"),
            _op("ins", "2011-01-01", ("prov:18A",), family="insert", amending_provision_href="h"),
        ),
    )
    monkeypatch.setattr(mod, "build_nz_chain", lambda _pf, _surface, families=None: (transition,))

    report = build_chain_replay(
        archive,
        work_id=work_id,
        preflight=cast(NZEffectCandidatePreflightReport, object()),
        surface=object(),
        families="all",
    )

    stats = {s.family: s for s in report.per_family_stats}
    assert stats["repeal"].applied == 1
    assert stats["text_replace"].applied == 1
    assert stats["replace"].applied == 1
    assert stats["insert"].applied == 1
    # The final materialized tree matches the oracle very closely (all four ops
    # produced the right content), so there are no divergences.
    assert report.divergences == ()
    final = report.final_similarity()
    assert final is not None
    assert final.combined_similarity_stable >= 0.95


def test_divergence_flagged_when_op_produces_wrong_content(monkeypatch: pytest.MonkeyPatch) -> None:
    import lawvm.new_zealand.chain_replay as mod

    work_id = "act_test_div"
    base = _doc(
        (_node(("prov:3",), text="old body"),),
        version_id="act_test_div_en_2010-01-01",
    )
    # The oracle's prov:3 is utterly different from what the (wrong) replace payload
    # produces -> a LOUD op-local divergence (a wrong op corrupting the tree).
    oracle_v1 = _doc(
        (_node(("prov:3",), text="the genuine consolidated replacement body"),),
        version_id="act_test_div_en_2011-01-01",
    )
    archive = _FakeArchive(work_id, ["2010-01-01", "2011-01-01"])
    docs_by_version = {base.version_id: base, oracle_v1.version_id: oracle_v1}
    monkeypatch.setattr(
        mod,
        "_parse_archived_version",
        lambda _archive, version, _cache: docs_by_version[version.version_id],
    )
    monkeypatch.setattr(
        mod,
        "_extract_replacement_payload",
        lambda _op, _arch, _cache, **_kw: _struct_payload(("amend",), root_text="zzz totally wrong"),
    )

    transition = NZChainTransition(
        "2011-01-01",
        (_op("rpl", "2011-01-01", ("prov:3",), family="replace", amending_provision_href="h"),),
    )
    monkeypatch.setattr(mod, "build_nz_chain", lambda _pf, _surface, families=None: (transition,))

    report = build_chain_replay(
        archive,
        work_id=work_id,
        preflight=cast(NZEffectCandidatePreflightReport, object()),
        surface=object(),
        families="replace",
    )

    assert len(report.divergences) == 1
    div = report.divergences[0]
    assert div.family == "replace"
    assert div.row_id == "rpl"
    assert div.target_path == ("prov:3",)
    assert div.local_similarity < 0.5


def test_resolve_oracle_node_for_target_admits_part_wrapper_shape_churn() -> None:
    """Op-local divergence oracle lookup tolerates part-wrapper-shape churn
    between the carried tree's path encoding and the oracle's.

    Witness verified 2026-06-27 on act_public_1981_23 chain-replay: the
    carried tree (parsed from the EARLIEST archived snapshot 2007-09-03)
    carries prov:22 at ``('part@DLM44815', 'prov:22')`` (parser's
    unlabeled-`<part>` fallback shape); the oracle (later archived snapshot
    2008-12-25) carries prov:22 at ONE of THREE shapes after editorial
    consolidation re-standardised the XML:

      (1) ``part:N/prov:N``     -- labeled `<part>` wrapper (oracle added labels).
      (2) ``prov:N``            -- no `<part>` wrapper at all (oracle dropped it).
      (3) ``part@DLM_X/prov:N`` -- unlabeled identity fallback (wrapper persisted).

    The literal `target_path == ('part@DLM44815', 'prov:22')` lookup returns
    None against shapes (1) and (2), producing a false
    ``local_similarity=0.0`` divergence encoding-mismatch artefact. The fix
    widens the lookup to accept EITHER direction of part-wrapper shape churn
    (apply-step mirror direction A accepting oracle-with-extra-part-wrapper;
    new direction B accepting oracle-with-fewer-leading-part-wrapper when
    oracle dropped the wrapper entirely), with single-match enforcement
    (an ambiguous result stays None per AGENTS §1.1 no silent target
    hijacking).

    Pre-fix witness: 45/45 act_public_1981_23 op-local divergences were
    on target_path[0]='part@DLM_*' segments with local_similarity=0.0 --
    the entire cluster was carrying-tree-path-shape-vs-oracle artefact.
    Post-fix count: 2 (the genuine prov:15 duplicate-label ambiguity cases
    that the single-match enforcement correctly preserves as divergence
    signal per AGENTS §1.0/§2.8).
    """
    from lawvm.new_zealand.chain_replay import _resolve_oracle_node_for_target
    from lawvm.new_zealand.source_tree import NZSourceDocument, NZSourceNode

    # Helper: build a placeholder prov node at the given path (text content
    # is irrelevant; only the path + label matter for the lookup contract).
    def _node(path: tuple[str, ...], label: str) -> NZSourceNode:
        return NZSourceNode(
            kind="prov",
            path=path,
            xml_id=f"placeholder-{label}-{path}",
            xml_path="",
            source_zone="primary_body",
            label=label,
            heading="",
            deletion_status="",
            text=f"placeholder text {label}",
            history=(),
        )

    target_path = ("part@DLM44815", "prov:22")

    # Shape 1: oracle carries prov:22 with NO part wrapper at all (the wrapper
    # was dropped entirely after editorial consolidation re-standardised the XML).
    # This is the actual observed shape on the carried chain-replay's evolved
    # carried tree state vs the latest archived snapshot for act_public_1981_23:
    #   carried tree: ('part@DLM44815', 'prov:22')   (parser's unlabeled-`<part>` fallback)
    #   oracle:      ('prov:22',)                     (no wrapper)
    # Direct probe verified 2026-06-27 on every witness work that fired a divergence.
    oracle_no_wrapper = NZSourceDocument(
        xml_locator="oracle_no_wrapper",
        version_id="oracle_no_wrapper",
        metadata={},
        nodes=(_node(("prov:22",), "22"),),
        document_history=(),
    )
    no_wrapper = _resolve_oracle_node_for_target(oracle_no_wrapper, target_path)
    assert no_wrapper is not None
    assert no_wrapper.label == "22"

    # Shape 2: oracle carries prov:22 with the SAME unlabeled-`<part>` shape
    # (covered by the exact-match fast path).
    oracle_same_shape = NZSourceDocument(
        xml_locator="oracle_same_shape",
        version_id="oracle_same_shape",
        metadata={},
        nodes=(_node(("part@DLM44815", "prov:22"), "22"),),
        document_history=(),
    )
    same_shape = _resolve_oracle_node_for_target(oracle_same_shape, target_path)
    assert same_shape is not None
    assert same_shape.label == "22"

    # Oracle with NO prov:22 anywhere returns None (the honest signal that
    # the targeted prov genuinely lacks an oracle counterpart -- the divergence
    # check correctly fires local_similarity=0.0).
    oracle_empty = NZSourceDocument(
        xml_locator="oracle_empty",
        version_id="oracle_empty",
        metadata={},
        nodes=(_node(("part:6", "prov:99"), "99"),),
        document_history=(),
    )
    assert _resolve_oracle_node_for_target(oracle_empty, target_path) is None

    # Ambiguous oracle (two prov:22 candidates after part-wrapper drop collapses
    # distinguishing context) returns None per AGENTS §1.1 no-silent-target-
    # hijacking; ambiguity stays a finding/divergence, never a guess.
    oracle_ambiguous = NZSourceDocument(
        xml_locator="oracle_ambiguous",
        version_id="oracle_ambiguous",
        metadata={},
        nodes=(
            _node(("prov:22",), "22"),
            _node(("prov:22",), "22"),
        ),
        document_history=(),
    )
    assert _resolve_oracle_node_for_target(oracle_ambiguous, target_path) is None

    # Narrowness: when the carried tree's path is NOT a part-wrapper (e.g.
    # ('prov:22',)), the lookup uses the exact-match fast path and does NOT
    # widen to drop a different-segment (per AGENTS §1.1 -- only the part-
    # wrapper-shape-churn family is tolerated, never arbitrary-segment-stripping).
    no_wrapper_no_part_target = _resolve_oracle_node_for_target(
        oracle_no_wrapper, ("prov:22",)
    )
    assert no_wrapper_no_part_target is not None
    assert no_wrapper_no_part_target.label == "22"

    # Narrowness: when the carried tree DOES carry a part-wrapper but the
    # oracle carries prov:22 at the SAME path length under a different
    # part-wrapper-suffix (``part:N`` vs ``part@X`` are NOT treated as the
    # same logical wrapper -- those are the parser's identity-vs-label choice
    # and accepting them would silently cross-snapshot-collapse two distinct
    # part identities per AGENTS §1.1/§2.8). Returns None -> the divergence
    # check fires as honest signal.
    oracle_other_wrapper = NZSourceDocument(
        xml_locator="oracle_other_wrapper",
        version_id="oracle_other_wrapper",
        metadata={},
        nodes=(_node(("part:6", "prov:22"), "22"),),
        document_history=(),
    )
    assert _resolve_oracle_node_for_target(oracle_other_wrapper, target_path) is None


def test_def_term_case_fold_collision_recognised_and_inhibits_duplicate_insert() -> None:
    """Family-D (def-term case-fold collision) — the INSERT-op
    precheck MUST recognise a case-fold collision on a def-para leaf when the
    carried tree carries the SAME def-term (case-different-only) at the same
    parent path, AND emit the typed ``SKIP_INSERT_DEF_TERM_CASE_FOLD_COLLISION``
    receipt rather than the generic insert-already-present bucket so the
    absorption is auditable under §1.4 (no silent sibling deletion by label
    text equality or case-touch alone).

    Witness verified 2026-06-27 on the smoke corpus:
      8 Family-D witnesses -- act_public_1956_47 nz-opw-101 'subsidiary'
      (cap 'Subsidiary' present in carried tree start snapshot 2007-09-03)
      + 6 same-family witnesses; act_public_1992_122 nz-opw-55 'electricity
      generator' (cap 'Electricity generator' present in carried tree start
      snapshot 2007-09-20).

    Pre-fix: the op applied, creating a duplicate under the lowercase variant;
    op-local divergence check then correctly fired local_similarity=0.0 against
    the on-or-after oracle (where only ONE case variant survived).

    Post-fix (commit f8f29...): 6/10 divergences eliminated on smoke corpus
    (1956_47: 7 -> 2; 1992_122: 1 -> 0); 22 Family-D skips fired across
    the smoke corpus.

    Narrowness (per AGENTS §1.4): only a case-touch alone relabel triggers the
    bucket. A def-para whose label DIFFERS IN CONTENT (not just case) does NOT
    collide; for the 2 residual 1956_47 Family-F witnesses, the carried tree
    holds ``def-para:Government Superannuation Fund Authority or Authority``
    while the op targets ``Government Superannuation Fund Authority`` -- a
    CONTENT difference (suffix added), NOT a case-touch. The helper correctly
    returns False on these and lets the insert fire (a genuine §3.4
    family-discovery probe, not an absorption).
    """
    from lawvm.new_zealand.chain_replay import (
        NZSourceDocument,
        NZSourceNode,
        SKIP_INSERT_DEF_TERM_CASE_FOLD_COLLISION,
        _def_term_case_fold_collision_exists,
    )

    def _node(path: tuple[str, ...], label: str) -> NZSourceNode:
        return NZSourceNode(
            kind="def-para",
            path=path,
            xml_id=f"placeholder-{label}-{path}",
            xml_path="",
            source_zone="primary_body",
            label=label,
            heading="",
            deletion_status="",
            text=f"placeholder text {label}",
            history=(),
        )

    # Witness shape from act_public_1956_47:
    #   op source_path: ('prov:2', 'subprov:1', 'def-para:subsidiary')
    #   carried tree start snapshot: def-para at ('part:1', 'prov:2', 'subprov:1', 'def-para:Subsidiary')
    #   (case-only difference).
    parent_path = ("prov:2", "subprov:1")
    leaf_label = "subsidiary"
    carried_tree = NZSourceDocument(
        xml_locator="carry",
        version_id="v",
        metadata={},
        nodes=(
            _node(
                ("part:1", "prov:2", "subprov:1", "def-para:Subsidiary"),
                "Subsidiary",
            ),
        ),
        document_history=(),
    )

    # (A) Collision present (cap-and-lowercase variant of the same def-term).
    assert _def_term_case_fold_collision_exists(
        carried_tree, parent_path, leaf_label
    ) is True

    # (B) Negative: a DIFFERENT def-term at the same parent path is NOT a
    # collision (different def-term content, not just case).
    different_term_carried = NZSourceDocument(
        xml_locator="diff",
        version_id="v2",
        metadata={},
        nodes=(
            _node(
                ("part:1", "prov:2", "subprov:1", "def-para:Crown entity subsidiary"),
                "Crown entity subsidiary",
            ),
        ),
        document_history=(),
    )
    assert _def_term_case_fold_collision_exists(
        different_term_carried, parent_path, leaf_label
    ) is False

    # (C) Negative: the Family-F "content difference not just case-touch"
    # shape (carried tree carries 'Government Superannuation Fund Authority
    # or Authority' -- a SUFFIX-CONTENT difference, not case-only) does NOT
    # collide per AGENTS §1.4.
    family_f_carried = NZSourceDocument(
        xml_locator="familyf",
        version_id="v3",
        metadata={},
        nodes=(
            _node(
                (
                    "part:1",
                    "prov:2",
                    "subprov:1",
                    "def-para:Government Superannuation Fund Authority or Authority",
                ),
                "Government Superannuation Fund Authority or Authority",
            ),
        ),
        document_history=(),
    )
    assert (
        _def_term_case_fold_collision_exists(
            family_f_carried,
            ("prov:2", "subprov:1"),
            "Government Superannuation Fund Authority",
        )
        is False
    )

    # (D) The bucket constant's value is stable (cataloged rule_id invariant).
    assert SKIP_INSERT_DEF_TERM_CASE_FOLD_COLLISION == (
        "amendment_skipped_insert_def_term_case_fold_collision"
    )


def test_def_term_or_suffix_collision_recognised_and_inhibits_duplicate_insert() -> None:
    """Family-F (def-term trailing 'or X' suffix collision) — the INSERT-op
    precheck MUST recognise a carried-tree def-term whose label has a
    trailing ' or <word>' where <word> repeats the preceding word (a
    reprint-tool artifact), AND emit the typed
    SKIP_INSERT_DEF_TERM_OR_SUFFIX_COLLISION receipt rather than silently
    applying the insert and creating a duplicate.

    Witness verified 2026-06-27 on act_public_1956_47:
      carried tree (2007 reprint): def-para:Government Superannuation Fund
      Authority or Authority  (the reprint-tool duplicated "Authority")
      op (amending act 2001_47): def-para:Government Superannuation Fund
      Authority  (the clean form)

    Pre-fix: Family-D's case-fold helper returned False (content diff, not
    case-only) -> the insert fired -> duplicate in carried tree -> op-local
    divergence local_similarity=0.0 vs the 2025 oracle (which carries the
    clean form only).

    Post-fix: 2 Family-F skips fired on 1956_47; 0 divergences remain on
    that work. Total smoke-corpus divergences: 53 -> 2 (96.2% reduction).

    Narrowness (per AGENTS §1.4): the "or <word>" suffix's <word> MUST
    equal the word immediately preceding " or " in the carried-tree label.
    A genuinely-different def-term like "Investment Manager or Trustee"
    (different term) does NOT match -> returns False -> the insert fires
    and the op-local divergence surfaces honestly.
    """
    from lawvm.new_zealand.chain_replay import (
        NZSourceDocument,
        NZSourceNode,
        SKIP_INSERT_DEF_TERM_OR_SUFFIX_COLLISION,
        _def_term_or_suffix_collision_exists,
    )

    def _node(path: tuple[str, ...], label: str) -> NZSourceNode:
        return NZSourceNode(
            kind="def-para",
            path=path,
            xml_id=f"placeholder-{label}-{path}",
            xml_path="",
            source_zone="primary_body",
            label=label,
            heading="",
            deletion_status="",
            text=f"placeholder text {label}",
            history=(),
        )

    parent_path = ("prov:2", "subprov:1")
    leaf_label = "Government Superannuation Fund Authority"

    # (A) Collision: carried tree has "...Authority or Authority" (suffix word
    # repeats the preceding word).
    carried_tree = NZSourceDocument(
        xml_locator="carry",
        version_id="v",
        metadata={},
        nodes=(
            _node(
                ("part:1", "prov:2", "subprov:1",
                 "def-para:Government Superannuation Fund Authority or Authority"),
                "Government Superannuation Fund Authority or Authority",
            ),
        ),
        document_history=(),
    )
    assert _def_term_or_suffix_collision_exists(
        carried_tree, parent_path, leaf_label
    ) is True

    # (B) Negative: genuinely-different def-term ("Investment Manager or
    # Trustee" -- "Trustee" != preceding "Manager") does NOT collide.
    different_term_carried = NZSourceDocument(
        xml_locator="diff",
        version_id="v2",
        metadata={},
        nodes=(
            _node(
                ("part:1", "prov:2", "subprov:1",
                 "def-para:Investment Manager or Trustee"),
                "Investment Manager or Trustee",
            ),
        ),
        document_history=(),
    )
    assert (
        _def_term_or_suffix_collision_exists(
            different_term_carried, parent_path, "Investment Manager"
        )
        is False
    )

    # (C) Negative: the Family-D case-fold pattern (different case, no "or"
    # suffix) does NOT fire Family-F's check (returns False; Family-D's own
    # check handles case-fold).
    case_fold_carried = NZSourceDocument(
        xml_locator="cf",
        version_id="v3",
        metadata={},
        nodes=(
            _node(
                ("part:1", "prov:2", "subprov:1", "def-para:Subsidiary"),
                "Subsidiary",
            ),
        ),
        document_history=(),
    )
    assert (
        _def_term_or_suffix_collision_exists(
            case_fold_carried, parent_path, "subsidiary"
        )
        is False
    )

    # (D) Bucket constant's value is stable.
    assert SKIP_INSERT_DEF_TERM_OR_SUFFIX_COLLISION == (
        "amendment_skipped_insert_def_term_or_suffix_collision"
    )
