"""Tests for ``lawvm self-consistency`` signal harvesting.

The pure helpers (category normalization, replay-log parsing) are tested
directly. The end-to-end projector row shape is exercised against a known
statute (1958/370) when a populated corpus is reachable, and skipped otherwise
so the suite stays runnable in a bare worktree.
"""
from __future__ import annotations

import pytest

import lawvm.tools.self_consistency as sc
from lawvm.tools.cli import _build_parser
from lawvm.tools.self_consistency import (
    ALL_SIGNAL_TYPES,
    _category,
    _classify_failed_reason,
    _occupancy_category,
    _occupancy_rows_from_findings,
    _occupancy_scope,
    _parse_replay_log,
)


# ---------------------------------------------------------------------------
# CLI jurisdiction dispatch
#
# The root-level ``-j`` selects the jurisdiction; the self-consistency
# subparser must inherit it (rather than redefine ``-j`` with a hard ``fi``
# default that clobbers ``-j uk`` back to ``fi`` and routes UK ids into the
# Finland corpus resolver).
# ---------------------------------------------------------------------------

def _parse_self_consistency(*argv: str):
    return _build_parser().parse_args(["self-consistency", *argv])


def test_root_jurisdiction_reaches_self_consistency() -> None:
    # ``-j uk self-consistency`` must surface as jurisdiction="uk" so main()
    # routes to the UK harness, not _main_fi (the FI corpus resolver).
    ns = _build_parser().parse_args(
        ["-j", "uk", "self-consistency", "--statutes", "ukpga/1991/22"]
    )
    assert ns.jurisdiction == "uk"


def test_root_jurisdiction_ee_reaches_self_consistency() -> None:
    ns = _build_parser().parse_args(["-j", "ee", "self-consistency"])
    assert ns.jurisdiction == "ee"


def test_self_consistency_jurisdiction_default_is_fi() -> None:
    ns = _parse_self_consistency("--statutes", "1958/370")
    assert ns.jurisdiction == "fi"


def test_self_consistency_jurisdiction_accepts_post_subcommand_flag() -> None:
    ns = _parse_self_consistency("-j", "uk", "--statutes", "ukpga/1991/22")
    assert ns.jurisdiction == "uk"


def test_self_consistency_main_routes_uk_without_fi_resolver(monkeypatch) -> None:
    # main() must dispatch jurisdiction="uk" to the UK harness and never touch
    # the Finland corpus store (whose absence previously crashed the UK path).
    called = {}

    def _fail_fi(_args):  # pragma: no cover - asserted not called
        raise AssertionError("UK dispatch reached the FI self-consistency path")

    monkeypatch.setattr(sc, "_main_fi", _fail_fi)
    monkeypatch.setattr(sc, "_main_uk", lambda _args: called.setdefault("uk", True))

    ns = _build_parser().parse_args(
        ["-j", "uk", "self-consistency", "--statutes", "ukpga/1991/22"]
    )
    sc.main(ns)
    assert called.get("uk") is True


def test_self_consistency_main_fails_loudly_for_unsupported_jurisdiction(monkeypatch) -> None:
    # nz/no have no self-consistency harness; main() must fail loudly rather than
    # silently fall through to the Finland projector (which crashes on finlex.farchive).
    def _fail_fi(_args):  # pragma: no cover - asserted not called
        raise AssertionError("nz dispatch reached the FI self-consistency path")

    monkeypatch.setattr(sc, "_main_fi", _fail_fi)
    ns = _build_parser().parse_args(["-j", "nz", "self-consistency"])
    with pytest.raises(SystemExit, match="not implemented for -j nz"):
        sc.main(ns)


# ---------------------------------------------------------------------------
# _category normalization
# ---------------------------------------------------------------------------

def test_category_prefers_reason_code() -> None:
    assert _category("anything at all", "ELAB.REJECTED_OPERATION") == "ELAB.REJECTED_OPERATION"


def test_category_collapses_momentti_numbers() -> None:
    # "momentti 2 not found" and "momentti 5 not found" share one bucket, and
    # the digit must not eat the following word "not".
    a = _category("momentti 2 not found")
    b = _category("momentti 5 not found")
    assert a == b
    assert "not found" in a
    assert a == "momentti N not found"


def test_category_collapses_section_letter_suffix() -> None:
    assert _category("section 10a missing") == _category("section 17 missing")


def test_category_normalizes_kind_label_and_lists() -> None:
    cat = _category("uncovered kumotaan: ['10a (drop)', '122', '123']")
    # Concrete labels and list contents are placeholdered away.
    assert "'X'" not in cat or cat.count("'X'") <= 1
    assert _category("uncovered kumotaan: ['1']") == _category("uncovered kumotaan: ['999']")


# ---------------------------------------------------------------------------
# Failed-reason classification
# ---------------------------------------------------------------------------

def test_classify_failed_reason_not_found_is_target_absent() -> None:
    assert _classify_failed_reason("momentti 2 not found") == "target_absent"
    assert _classify_failed_reason("master section:111 not found") == "target_absent"


def test_classify_failed_reason_unhandled_is_unhandled_op() -> None:
    assert _classify_failed_reason("section not found or unhandled op") == "target_absent"
    assert _classify_failed_reason("unhandled non-section op") == "unhandled_op"


# ---------------------------------------------------------------------------
# Replay-log parsing (the silently-swallowed signals)
# ---------------------------------------------------------------------------

def test_parse_log_recovers_momentti_target_absent() -> None:
    log = "  [1977/604] REPEAL 111 § 2 mom → FAILED (momentti 2 not found)\n"
    rows = _parse_replay_log("1958/370", log)
    assert len(rows) == 1
    row = rows[0]
    assert row["statute_id"] == "1958/370"
    assert row["amendment_id"] == "1977/604"
    assert row["signal_type"] == "target_absent"
    assert row["category"] == "momentti N not found"
    assert "REPEAL 111" in row["description"]
    assert "[1977/604]" not in row["description"]


def test_parse_log_coverage_dropped_op() -> None:
    log = "  [1968/493] Coverage: 34 units, 32 claimed, 16 uncovered\n"
    rows = _parse_replay_log("1958/370", log)
    assert len(rows) == 1
    row = rows[0]
    assert row["signal_type"] == "coverage_gap"
    assert row["category"] == "claimed<units (dropped op?)"
    assert row["amendment_id"] == "1968/493"


def test_parse_log_coverage_clean_is_ignored() -> None:
    # claimed == units and zero uncovered is internally consistent: no signal.
    log = "  [1990/100] Coverage: 5 units, 5 claimed, 0 uncovered\n"
    assert _parse_replay_log("x/1", log) == []


def test_parse_log_skipped_amendment() -> None:
    log = "  [1999/123] not found in corpus — skipping\n"
    rows = _parse_replay_log("1958/370", log)
    assert len(rows) == 1
    assert rows[0]["signal_type"] == "skipped_amendment"
    assert rows[0]["amendment_id"] == "1999/123"


def test_parse_log_unhandled_op() -> None:
    log = "  140 § → FAILED (section not found or unhandled op)\n"
    rows = _parse_replay_log("x/1", log)
    assert len(rows) == 1
    assert rows[0]["signal_type"] == "target_absent"  # "not found" wins


def test_parse_log_arrow_ascii_variant() -> None:
    log = "  [2000/1] 5 § -> FAILED (unhandled non-section op)\n"
    rows = _parse_replay_log("x/1", log)
    assert len(rows) == 1
    assert rows[0]["signal_type"] == "unhandled_op"
    assert rows[0]["amendment_id"] == "2000/1"


def test_all_signal_types_is_complete() -> None:
    # Guard against a signal_type string drifting out of the canonical tuple.
    assert "target_absent" in ALL_SIGNAL_TYPES
    assert "coverage_gap" in ALL_SIGNAL_TYPES
    assert "occupancy_violation" in ALL_SIGNAL_TYPES
    assert "invariant_lint_warning" in ALL_SIGNAL_TYPES
    assert len(set(ALL_SIGNAL_TYPES)) == len(ALL_SIGNAL_TYPES)


def test_project_invariant_signals_row_shape() -> None:
    meta = {
        "typed_invariant_violations": [
            {
                "kind": "duplicate_label",
                "path": "body/section:1",
                "child_kind": "section",
                "label": "5a",
                "surface": "replay_fold_tree",
            },
        ],
        "flattened_sublist_warnings": [
            {
                "kind": "flattened_sublist_mixed_family",
                "path": "body/section:4/subsection:1",
                "node_kind": "subsection",
            },
        ],
    }
    rows = sc._project_invariant_signals("1994/1472", meta, ())
    assert len(rows) == 2
    assert rows[0]["signal_type"] == "invariant_violation"
    assert rows[0]["category"] == "duplicate_label"
    assert rows[1]["signal_type"] == "invariant_lint_warning"
    assert rows[1]["category"] == "flattened_sublist_mixed_family"


# ---------------------------------------------------------------------------
# Occupancy-violation categorisation + row projection
# ---------------------------------------------------------------------------

def test_occupancy_category_repeal_of_absent() -> None:
    assert _occupancy_category("REPEAL", "absent") == "repeal-of-absent"
    # Section letter / numbers in the occupancy class never appear, so the
    # bucket is stable across statutes.
    assert _occupancy_category("REPLACE", "absent") == "replace-of-absent"


def test_occupancy_category_insert_into_occupied() -> None:
    # A substantive slot normalises to the readable "occupied" shorthand and
    # an INSERT uses the "into" relation.
    assert _occupancy_category("INSERT", "substantive") == "insert-into-occupied"
    assert _occupancy_category("INSERT", "tombstone") == "insert-into-tombstone"


def test_occupancy_category_unknown_action_defaults_to_of() -> None:
    assert _occupancy_category("", "absent") == "op-of-absent"


def test_occupancy_scope_parses_chapter_and_section() -> None:
    assert _occupancy_scope("[1992/1439] INSERT 2 luku 17 §", "17") == "2 luku 17 §"
    assert _occupancy_scope("[1992/1167] REPEAL 136a §", "136a") == "136a §"
    # Fallback to the typed target label when the ctx_label carries no §.
    assert _occupancy_scope("[2000/1] something", "10a") == "10a §"


def test_occupancy_rows_from_findings_projects_violation() -> None:
    class _F:
        kind = "APPLY.OCCUPANCY_POLICY_VIOLATION"
        source_statute = "1992/1167"
        detail = {
            "ctx_label": "[1992/1167] REPEAL 136a §",
            "legacy_action": "REPEAL",
            "target_label": "136a",
            "current_occupancy": "absent",
            "allowed_from": ["substantive", "tombstone"],
        }

    rows = _occupancy_rows_from_findings("1958/370", "1992/1167", [_F()])
    assert len(rows) == 1
    row = rows[0]
    assert row["statute_id"] == "1958/370"
    assert row["amendment_id"] == "1992/1167"
    assert row["signal_type"] == "occupancy_violation"
    assert row["category"] == "repeal-of-absent"
    assert row["description"] == "REPEAL 136a §"
    assert row["target_scope"] == "136a §"
    assert "is absent, not in allowed_from {substantive, tombstone}" in row["reason"]


def test_occupancy_rows_skip_allowed_non_primary_note() -> None:
    # The "allowed but not primary expected" disposition (e.g. a REPLACE landing
    # on a tombstone — the legitimate reenactment lane) is not a violation.
    class _Note:
        kind = "APPLY.OCCUPANCY_POLICY_VIOLATION"
        source_statute = "1981/499"
        detail = {
            "ctx_label": "[1981/499] INSERT 11 luku 114 §",
            "legacy_action": "INSERT",
            "target_label": "114",
            "current_occupancy": "tombstone",
            "allowed_from": ["absent", "scaffold", "tombstone"],
            "allowed_non_primary": True,
        }

    assert _occupancy_rows_from_findings("1958/370", "1981/499", [_Note()]) == []


def test_occupancy_rows_ignore_other_finding_kinds() -> None:
    class _Other:
        kind = "ELAB.REJECTED_OPERATION"
        source_statute = "x/1"
        detail = {"current_occupancy": "absent", "legacy_action": "REPEAL"}

    assert _occupancy_rows_from_findings("x/1", "x/1", [_Other()]) == []


# ---------------------------------------------------------------------------
# End-to-end projector row shape (corpus-backed; skipped without a corpus)
# ---------------------------------------------------------------------------

def _corpus_store_or_skip():
    from lawvm.finland.corpus import get_corpus_store

    try:
        store = get_corpus_store()
        if store.read_source("1958/370") is None:
            pytest.skip("1958/370 not present in reachable corpus")
        return store
    except Exception as exc:  # corpus archive missing in a bare worktree
        pytest.skip(f"corpus not reachable: {type(exc).__name__}")


def test_projector_row_shape_and_known_signals() -> None:
    store = _corpus_store_or_skip()
    rows, errors = sc._project_self_consistency("1958/370", store)
    assert errors == []
    assert rows, "expected self-consistency signals for 1958/370"

    required_keys = {
        "statute_id",
        "amendment_id",
        "signal_type",
        "category",
        "description",
        "target_scope",
        "reason",
    }
    for row in rows:
        assert required_keys <= set(row), f"row missing keys: {row}"
        assert row["statute_id"] == "1958/370"
        assert row["signal_type"] in ALL_SIGNAL_TYPES

    # Regression guard: 1958/370 once exhibited the 1968/493 §111 dropped-op and
    # the downstream 1977/604 §111 momentti-not-found (target_absent). Both were
    # root-caused and fixed (the johtolause provenance-comma drop in
    # annotate_statute_citations), so the projector must NOT report a 1977/604
    # §111 target_absent here — its presence would mean the §111 fix regressed.
    assert not any(
        r["signal_type"] == "target_absent"
        and r["amendment_id"] == "1977/604"
        and "111" in r["description"]
        for r in rows
    ), "1977/604 §111 momentti-not-found must stay fixed (no target_absent)"

    # Occupancy is now derived from the AUTHORITATIVE full replay, not a
    # lightweight per-amendment fold.  1992/1167 repeals §136a, which a fold
    # without chapter-seeding sees as absent — but §136a is INSERTed by 1973/589
    # and IS present in the full cumulative replay, so the full replay recovers
    # the occupancy and reports NO violation here.  The lightweight fold reported
    # a false positive; the full-replay source must NOT.
    occupancy = [r for r in rows if r["signal_type"] == "occupancy_violation"]
    assert not any(
        r["amendment_id"] == "1992/1167" and "136a" in r["description"]
        for r in occupancy
    ), "1992/1167 §136a is present in the full replay — no occupancy_violation"

    # The upstream 1968/493 dropped-op coverage gap remains a self-consistency
    # signal for the same statute.
    coverage = [r for r in rows if r["signal_type"] == "coverage_gap"]
    assert any(
        r["amendment_id"] == "1968/493" for r in coverage
    ), "expected 1968/493 coverage gap signal"


# ---------------------------------------------------------------------------
# Regression: compound sub-item (alakohta) INSERT must be idempotent when an
# earlier op in the same amendment already carried the sub-item.
#
# 2008/668 §5 1 mom 1 kohta: amendment 2023/710 both REPLACEs item:1 (whose
# source snapshot already includes the newly-authored `o` alakohta) AND emits
# an explicit `lisätään ... uusi o alakohta` INSERT for the same `o`. The
# compound-insert path used to append `o` unconditionally, producing a paragraph
# with two `o` subparagraphs. The transient sparse render hid it, but a later
# whole-section snapshot (2025/773) re-materialized the full paragraph and the
# duplicate `o` surfaced as a duplicate_label invariant violation — and, for
# 2008/668, as a VISIBLE double `o)` in the consolidated section text.
#
# 2009/1599 §5 (`d`) and 1948/608 §60b (`g`) are the tree-level phantom siblings
# of the same mechanism (text rendering collapsed the identical adjacent nodes,
# but the structural invariant detector still fired). All three must now be
# free of the duplicate.
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize("statute_id", ["2008/668", "2009/1599", "1948/608"])
def test_compound_subitem_insert_is_idempotent_no_duplicate_label(statute_id: str) -> None:
    from lawvm.finland.corpus import get_corpus_store
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, call_replay_xml
    from lawvm.core.invariant_profiles import (
        collect_tree_invariant_violations,
        structural_tree_all_profile,
    )

    try:
        store = get_corpus_store()
        if store.read_source(statute_id) is None:
            pytest.skip(f"{statute_id} not present in reachable corpus")
    except Exception as exc:  # corpus archive missing in a bare worktree
        pytest.skip(f"corpus not reachable: {type(exc).__name__}")

    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(parent_id=statute_id, mode="legal_pit", quiet=True),
    )
    profile = structural_tree_all_profile("test.compound_subitem_insert_idempotent")
    violations = collect_tree_invariant_violations(master.state.ir, profile)
    duplicate_violations = [
        violation for violation in violations if "duplicate" in violation.message.lower()
    ]
    assert duplicate_violations == [], (
        f"{statute_id}: compound sub-item INSERT must not double-emit a label; "
        f"got {[v.message for v in duplicate_violations]}"
    )


def test_2008_668_section_5_has_single_o_alakohta() -> None:
    """2008/668 §5's consolidated text must carry exactly one `o)` alakohta.

    Text-visible half of the regression: before the idempotent-insert fix the
    consolidated §5 rendered the `o) varautumiseen käytetyistä resursseista;`
    alakohta twice (replay count 2 vs oracle 1).
    """
    from lawvm.finland.corpus import get_corpus_store
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, call_replay_xml
    from lawvm.core.ir_helpers import irnode_to_text

    try:
        store = get_corpus_store()
        if store.read_source("2008/668") is None:
            pytest.skip("2008/668 not present in reachable corpus")
    except Exception as exc:
        pytest.skip(f"corpus not reachable: {type(exc).__name__}")

    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(parent_id="2008/668", mode="legal_pit", quiet=True),
    )

    def _find_section_5(node):
        kind = str(getattr(node, "kind", "")).split(".")[-1].lower()
        if kind == "section" and str(getattr(node, "label", "")) == "5":
            return node
        for child in getattr(node, "children", ()) or ():
            found = _find_section_5(child)
            if found is not None:
                return found
        return None

    section = _find_section_5(master.state.ir)
    assert section is not None, "expected §5 in replayed 2008/668"
    text = irnode_to_text(section)
    assert text.count("o) varautumiseen käytetyistä resursseista") == 1, (
        "§5 must render the `o)` alakohta exactly once (no duplicate)"
    )
