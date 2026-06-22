"""Unit + e2e tests for the LOCUS snapshot adapter (the static-snapshot producer).

These tests drive ``export_snapshot_pack`` with SYNTHETIC rows (the ``rows=``
injection path), so they never touch duckdb / the 2.2 GB LOCUS parquet and run in
milliseconds. They assert the load-bearing invariants:

* address induction from the dotted section numbering — clean ``N.N.N`` works,
  the markdown ``#`` depth is irrelevant, and un-inducible headers (titles,
  subsection markers, ``§``-style numbers, nulls) yield ``None`` (→ a typed
  residual, never a silent drop);
* a synthetic work packs → ``check-pack`` ``VALID_WITH_UNSUPPORTED_LAYERS`` (the
  analytical overlay is an optional layer) + ``VALID_CLEAN`` certification +
  ``TOTAL_WITH_RESIDUALS`` / ``TOTAL`` totality (every addressable node owned,
  every gap typed);
* the analytical scores are an OVERLAY anchored to ``content_leaf_hash`` and
  NEVER enter any legal-state / selection root (determinism firewall);
* the pack carries NO dense ``active_at`` / ``display_nodes`` escape and no
  SQLite (the sparse-pack invariant the FI exporter also enforces);
* the genesis is one ``observed_codification_snapshot`` event with no transitions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lawvm.substrate.checker import (
    CheckMode,
    IntegrityVerdict,
    TopLineVerdict,
    check_pack,
)
from lawvm.substrate.locus import (
    AddressInducer,
    LocusRow,
    METHOD_EXACT_DOTTED,
    METHOD_SEQUENTIAL_STACK,
    METHOD_WORD_CONTAINER,
    SNAPSHOT_DATE,
    WorkKey,
    export_snapshot_pack,
    induce_address,
    load_snapshot_pack_for_check,
)
from lawvm.substrate.totality import TotalityVerdict


# --------------------------------------------------------------------------- #
# Address induction                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "header,expected",
    [
        ("### 1.05.010 Name of municipality.", "title:1/chapter:05/section:010"),
        ("## 1.10.030 Time ordinances take effect.", "title:1/chapter:10/section:030"),
        # markdown depth is irrelevant — the dotted number is authoritative.
        ("# 2.05.010 Equal employment.", "title:2/chapter:05/section:010"),
        ("1.05.010 No leading hash.", "title:1/chapter:05/section:010"),
        # deeper numbering (San Jose) is still owned via level_<n> segments.
        ("### 1.05.010.020 Deep.", "title:1/chapter:05/section:010/subsection:020"),
        # two-segment dotted is a valid (shallower) address.
        ("### 1.05 Chapter heading number.", "title:1/chapter:05"),
        # §-style ordinance numbering is now stripped to its dotted skeleton (the
        # +13.5pp quick win) — §/Sec./Section labels precede the real number.
        ("§ 90.01 DEFINITIONS.", "title:90/chapter:01"),
        ("Sec. 38-1014. No parking.", "title:38/chapter:1014"),
        ("Section 1.05.010 Name.", "title:1/chapter:05/section:010"),
        # dash-dotted is an equally-authoritative absolute convention.
        ("1-2-1: REPEAL.", "title:1/chapter:2/section:1"),
    ],
)
def test_induce_address_dotted(header: str, expected: str) -> None:
    induced = induce_address(header)
    assert induced is not None, header
    assert induced.address_path == expected
    assert induced.method == METHOD_EXACT_DOTTED


@pytest.mark.parametrize(
    "header",
    [
        "GENERAL PROVISIONS",  # chapter/article TITLE heading (no number)
        "(a) Definitions.",  # bare subsection marker (no parent in the stateless primitive)
        "1. Contents.",  # bare single ordinal = list marker, NOT an absolute address
        "Article 2. Police Department",  # word container — needs the document-order fold
        "PREAMBLE",
        None,  # null header
        "",  # empty header
    ],
)
def test_induce_address_residualizes(header: str | None) -> None:
    """The stateless primitive yields None for non-absolute headers (→ typed residual).

    Word containers and relative ordinals need the document-order
    :class:`AddressInducer` fold (a running parent stack); the stateless
    ``induce_address`` only owns absolute dotted/dashed numbers.
    """
    assert induce_address(header) is None


# --------------------------------------------------------------------------- #
# Document-order stack fold (the max-recall path)                             #
# --------------------------------------------------------------------------- #


def test_fold_word_container_and_relative_items() -> None:
    """The fold resolves word containers + relative ordinals against a parent stack."""
    inducer = AddressInducer()
    # A word container pushes a typed container segment.
    a = inducer.induce("## ARTICLE II")
    assert a is not None and a.method == METHOD_WORD_CONTAINER
    assert a.address_path == "article:II"
    # A relative ordinal appends under the current parent (positionally unique).
    b = inducer.induce("(a) Definitions.")
    assert b is not None and b.method == METHOD_SEQUENTIAL_STACK
    assert b.address_path == "article:II/item:a"
    # A sibling relative item stays flat (one item level), not ever-deepening.
    c = inducer.induce("(b) Scope.")
    assert c is not None and c.address_path == "article:II/item:b"


def test_fold_absolute_resets_stack() -> None:
    """An absolute dotted number RESETS the path stack (authoritative skeleton)."""
    inducer = AddressInducer()
    inducer.induce("ARTICLE I")
    inducer.induce("(1) something")
    reset = inducer.induce("### 5.10.020 Real section.")
    assert reset is not None and reset.method == METHOD_EXACT_DOTTED
    assert reset.address_path == "title:5/chapter:10/section:020"


def test_fold_container_rank_reopens_at_right_level() -> None:
    """A sibling/ancestor container reopens at its level, not nested under a deeper one."""
    inducer = AddressInducer()
    inducer.induce("CHAPTER 1")
    inducer.induce("ARTICLE 5")  # deeper than chapter
    ch2 = inducer.induce("CHAPTER 2")  # pops the article, replaces the chapter
    assert ch2 is not None and ch2.address_path == "chapter:2"


def test_fold_orphan_relative_residualizes() -> None:
    """A relative marker with no established parent stays None (→ typed residual)."""
    inducer = AddressInducer()
    assert inducer.induce("(a) orphan with no parent") is None


# --------------------------------------------------------------------------- #
# Synthetic work fixture                                                        #
# --------------------------------------------------------------------------- #


def _scores(**kw: float | None) -> dict[str, float | None]:
    base: dict[str, float | None] = {
        "enforcement_discretion": None,
        "opacity": None,
        "paternalism": None,
        "problem_salience": None,
    }
    base.update(kw)
    return base


def _synthetic_rows() -> list[LocusRow]:
    return [
        LocusRow(0, "### 1.05.010 Name.", "The name is Testville.", False, "Context", None,
                 _scores(enforcement_discretion=0.1, opacity=0.2, problem_salience=0.5)),
        LocusRow(1, "### 1.05.020 Seal.", "The seal is round.", True, "Process", "admin",
                 _scores(enforcement_discretion=0.3, opacity=0.4, paternalism=0.1, problem_salience=0.9)),
        # constant text reused -> content-leaf dedup.
        LocusRow(2, "### 1.05.030 Same.", "The seal is round.", True, "Process", None, _scores()),
        LocusRow(3, "## 2.10.010 Records.", "Records are public.", True, "Process", "records", _scores()),
        # un-inducible header -> typed residual.
        LocusRow(4, "GENERAL PROVISIONS", "heading row", False, "Context", None, _scores()),
        # duplicate address -> typed residual (first occurrence keeps the address).
        LocusRow(5, "### 1.05.010 Dup.", "duplicate addr text", False, "Context", None, _scores()),
    ]


@pytest.fixture()
def synthetic_pack(tmp_path: Path) -> Path:
    out = tmp_path / "pack"
    key = WorkKey(state="zz", city="testville", county=None, jurisdiction_type="cities")
    result = export_snapshot_pack("", key, out, rows=_synthetic_rows())
    assert result.n_addressable_leaves == 4
    return out


# --------------------------------------------------------------------------- #
# Pack-level invariants                                                         #
# --------------------------------------------------------------------------- #


def test_snapshot_pack_is_valid_and_total(synthetic_pack: Path) -> None:
    pack = load_snapshot_pack_for_check(synthetic_pack)
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    # The overlay is an optional layer with an unknown-to-checker schema → the
    # determinism firewall surfaces it as VALID_WITH_UNSUPPORTED_LAYERS, never
    # an INVALID pack.
    assert verdict.integrity is IntegrityVerdict.VALID_WITH_UNSUPPORTED_LAYERS, [
        v.to_canonical_dict() for v in verdict.violations
    ]
    assert verdict.top_line_verdict is TopLineVerdict.VALID_WITH_UNSUPPORTED_LAYERS
    assert verdict.certification.value == "VALID_CLEAN"
    # Totality: every addressable node owned (selected leaf or typed-reason
    # container); the two typed residuals qualify it to TOTAL_WITH_RESIDUALS,
    # never silently TOTAL and never INCOMPLETE (no silent gap).
    assert verdict.totality.verdict is TotalityVerdict.TOTAL_WITH_RESIDUALS, (
        verdict.totality.to_canonical_dict()
    )
    assert verdict.totality.owned_nodes + verdict.totality.typed_non_selection_nodes == (
        verdict.totality.addressable_nodes
    )
    assert not verdict.totality.shortfalls


def test_typed_residuals_named(synthetic_pack: Path) -> None:
    """The un-inducible header + duplicate address are TYPED residuals, not drops."""
    proof_rows = _read_layer(synthetic_pack / "proof" / "proof.jsonl")
    residuals = [
        r["object"] for r in proof_rows if r["object"].get("schema") == "lawvm.residual.v1"
    ]
    kinds = {r["kind"] for r in residuals}
    assert kinds == {"locus_header_unparsed", "locus_duplicate_address"}
    # Self-evidencing: the offending header text is embedded in the detail.
    details = " ".join(r["detail"] for r in residuals)
    assert "GENERAL PROVISIONS" in details
    assert "title:1/chapter:05/section:010" in details


def test_induction_method_breakdown_is_visible(synthetic_pack: Path) -> None:
    """The induction-method breakdown is surfaced as ``benign`` coverage rows.

    Recall-visibility: how much of the owned address tree rests on each method
    (authoritative vs heuristic) is in the proof layer, not hidden. They use the
    ``benign`` coverage class so the closed 4-class exhaustiveness check holds.
    """
    proof_rows = _read_layer(synthetic_pack / "proof" / "proof.jsonl")
    method_rows = [
        r["object"]
        for r in proof_rows
        if r["object"].get("schema") == "lawvm.coverage_row.v1"
        and "induction_method:" in str(r["object"].get("detail", ""))
    ]
    assert method_rows, "expected induction-method coverage rows"
    assert all(r["coverage_class"] == "benign" for r in method_rows)
    details = " ".join(r["detail"] for r in method_rows)
    # All four synthetic dotted rows are authoritative exact_dotted induction.
    assert "induction_method:exact_dotted" in details


def test_scores_are_overlay_not_legal_state(synthetic_pack: Path) -> None:
    """The analytical scores live ONLY in the overlay layer, anchored to the leaf."""
    overlay_rows = _read_layer(synthetic_pack / "overlay" / "overlay.jsonl")
    assert overlay_rows, "expected analytical overlay rows"
    for r in overlay_rows:
        body = r["object"]
        assert body["schema"] == "lawvm.overlay.v1"
        assert body["anchor"]["anchor_kind"] == "content_leaf"
        # The determinism firewall: the enricher can never mutate legal state.
        assert body["authority"]["surface_only"] is True
        assert body["authority"]["replay_authorized"] is False
        assert body["producer"]["determinism"] == "external_generated"
    # No score column name leaks into the legal-state (base/state) layers.
    score_cols = {"enforcement_discretion", "opacity", "paternalism", "problem_salience"}
    for layer in ("base", "state"):
        for r in _read_layer(synthetic_pack / layer / f"{layer}.jsonl"):
            _assert_keys_absent(r, score_cols, synthetic_pack / layer)


def test_genesis_is_observed_snapshot_no_transitions(synthetic_pack: Path) -> None:
    base_rows = _read_layer(synthetic_pack / "base" / "base.jsonl")
    genesis = [
        r["object"]
        for r in base_rows
        if r["object"].get("schema") == "lawvm.initial_state_event.v1"
    ]
    assert len(genesis) == 1
    assert genesis[0]["genesis_kind"] == "observed_codification_snapshot"
    assert genesis[0]["effective_date"] == SNAPSHOT_DATE
    # creation_event_id is the immutable manifestation id (snapshot genesis anchor).
    assert genesis[0]["creation_event_id"].startswith("sha256:")
    # No transitions: the trace layer is empty.
    trace = _read_layer(synthetic_pack / "trace" / "trace.jsonl")
    assert trace == []


def test_content_leaf_dedup(synthetic_pack: Path) -> None:
    """Two sections with identical text share ONE content leaf (the dedup win)."""
    base_rows = _read_layer(synthetic_pack / "base" / "base.jsonl")
    leaves = [
        r["object"] for r in base_rows if r["object"].get("schema") == "lawvm.content_leaf.v1"
    ]
    texts = [leaf["text"] for leaf in leaves]
    assert texts.count("The seal is round.") == 1  # 1.05.020 + 1.05.030 share it
    hashes = [leaf["content_leaf_hash"] for leaf in leaves]
    assert len(hashes) == len(set(hashes))


def test_no_dense_keys_or_sqlite(synthetic_pack: Path) -> None:
    forbidden = {"active_at", "display_nodes", "active_node", "display_node"}
    for jsonl in synthetic_pack.rglob("*.jsonl"):
        for row in _read_layer(jsonl):
            _assert_no_forbidden_keys(row, forbidden, jsonl)
    assert not list(synthetic_pack.rglob("*.db"))
    assert not list(synthetic_pack.rglob("*.sqlite"))


def test_hash_prefix_and_ensure_ascii(synthetic_pack: Path) -> None:
    for jsonl in synthetic_pack.rglob("*.jsonl"):
        for row in _read_layer(jsonl):
            oh = row["object_hash"]
            assert oh.startswith("sha256:"), oh
            assert not oh.startswith("sha256:sha256:"), oh


def test_no_overlay_flag_excludes_overlay(tmp_path: Path) -> None:
    """--no-overlay yields a pure legal-state pack with an empty overlay layer."""
    out = tmp_path / "pack_no_overlay"
    key = WorkKey(state="zz", city="testville", county=None, jurisdiction_type="cities")
    result = export_snapshot_pack("", key, out, rows=_synthetic_rows(), emit_overlay=False)
    assert result.n_overlay_rows == 0
    overlay = _read_layer(out / "overlay" / "overlay.jsonl")
    assert overlay == []
    # With no overlay layer rows, the pack is fully VALID (no unsupported schema).
    pack = load_snapshot_pack_for_check(out)
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    assert verdict.integrity is IntegrityVerdict.VALID, [
        v.to_canonical_dict() for v in verdict.violations
    ]
    assert verdict.top_line_verdict is TopLineVerdict.VALID_CLEAN


def test_county_work_key() -> None:
    """A county work keys on county, not city (jurisdiction-neutral selector)."""
    key = WorkKey(state="ca", city=None, county="los_angeles_county", jurisdiction_type="counties")
    assert key.work_id == "us-local:counties:ca/los_angeles_county"
    assert "Los Angeles County" in key.title


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _read_layer(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _assert_no_forbidden_keys(obj: Any, forbidden: set[str], where: Path) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key not in forbidden, f"forbidden dense key {key!r} in {where}"
            _assert_no_forbidden_keys(value, forbidden, where)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_forbidden_keys(item, forbidden, where)


def _assert_keys_absent(obj: Any, names: set[str], where: Path) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key not in names, f"legal-state leak: {key!r} in {where}"
            _assert_keys_absent(value, names, where)
    elif isinstance(obj, list):
        for item in obj:
            _assert_keys_absent(item, names, where)
