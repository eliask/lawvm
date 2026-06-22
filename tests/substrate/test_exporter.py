"""Unit + e2e tests for the ``pack-work`` exporter (P3).

The unit tests drive the exporter with a SYNTHETIC adapter (a tiny replay bundle
+ a per-date IR tree builder) so they run in milliseconds and never touch the
5.9 GB farchive. They assert the load-bearing invariants:

* the emitted pack passes ``check_pack`` with a clean VALID verdict;
* content leaves are deduped by text-only hash (the 80x win);
* the pack carries NO dense ``active_at`` / ``display_nodes`` keys anywhere
  (map §8 gotcha 3);
* every semantic hash is ``"sha256:"``-prefixed exactly once and ``ensure_ascii``
  is honored (a non-ASCII glyph is escaped in the on-disk bytes).

A marked slow e2e replays a real FI statute end to end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.substrate import exporter as exporter_mod
from lawvm.substrate.checker import (
    CheckMode,
    IntegrityVerdict,
    check_pack,
)
from lawvm.substrate.exporter import export_work_pack, load_pack_for_check


# --------------------------------------------------------------------------- #
# Synthetic adapter                                                            #
# --------------------------------------------------------------------------- #


class _FakeProfile:
    def canonical_statute_id(self, raw: str) -> str:
        # Accept "1/2000" or "2000/1"; normalize to num/year.
        a, b = raw.split("/")
        if len(a) == 4:  # year/num
            return f"{b}/{a}"
        return raw

    def engine_statute_id(self, canonical: str) -> str:
        num, year = canonical.split("/")
        return f"{year}/{num}"

    def commencement_date(self, _timelines: dict[Any, Any]) -> str:
        return "2000-01-01"


def _fake_bundle() -> Any:
    from lawvm.tools.export_transition_graph import ReplayBundle

    return ReplayBundle(
        statute_id="1/2000",
        engine_id="2000/1",
        title="Testilaki ä ö",  # non-ASCII on purpose
        result=None,
        lo_ops=[],
        timelines={},
        change_dates=["2000-01-01", "2005-01-01", "2010-01-01"],
        replay_findings=[],
        failed_ops=[],
        source_pathologies=[],
    )


def _section(label: str, text: str) -> IRNode:
    # A section whose single covering unit (it has no subsection child) is itself.
    return IRNode(kind=IRNodeKind("section"), label=label, text=text)


def _tree_for_date(_bundle: Any, date: str) -> IRNode:
    """A tiny statute tree that changes across the three dates.

    section:1 stays constant (its text is reused so its content leaf dedups
    across all three dates). section:2 changes text at 2005. section:3 appears
    only from 2010.
    """
    children = [
        _section("1", "Pysyvä teksti ä"),  # constant -> dedup
    ]
    if date < "2005-01-01":
        children.append(_section("2", "Alkuperäinen kakkospykälä"))
    else:
        children.append(_section("2", "Muutettu kakkospykälä"))
    if date >= "2010-01-01":
        children.append(_section("3", "Uusi kolmospykälä"))
    return IRNode(kind=IRNodeKind("body"), label=None, text="", children=tuple(children))


class _FakeAdapter:
    def __init__(self) -> None:
        self.profile = _FakeProfile()
        self.tree_materializer = _tree_for_date

    def replay_runner(self, _engine_id: str, *, profile: Any) -> Any:
        return _fake_bundle()


@pytest.fixture()
def synthetic_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    adapter = _FakeAdapter()
    monkeypatch.setattr(
        "lawvm.tools.transition_graph_jurisdictions.transition_graph_adapter_for_jurisdiction",
        lambda _j: adapter,
    )
    out = tmp_path / "pack"
    result = export_work_pack("1/2000", out, jurisdiction="fi", quiet=True)
    assert result.n_change_dates == 3
    return out


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #


def test_synthetic_pack_is_valid(synthetic_pack: Path) -> None:
    pack = load_pack_for_check(synthetic_pack)
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    assert verdict.integrity is IntegrityVerdict.VALID, [
        v.to_canonical_dict() for v in verdict.violations
    ]
    # A residual-free synthetic pack folds to a clean certification.
    assert verdict.top_line_verdict.value == "VALID_CLEAN", verdict.to_canonical_dict()


def test_content_leaf_dedup(synthetic_pack: Path) -> None:
    """section:1's constant text must produce ONE content leaf, not three."""
    base_rows = _read_layer(synthetic_pack / "base" / "base.jsonl")
    leaves = [
        r["object"]
        for r in base_rows
        if r["object"].get("schema") == "lawvm.content_leaf.v1"
    ]
    texts = [leaf["text"] for leaf in leaves]
    # The constant "Pysyvä teksti ä" appears exactly once despite three dates.
    assert texts.count("Pysyvä teksti ä") == 1
    # All content-leaf hashes are unique (dedup invariant).
    hashes = [leaf["content_leaf_hash"] for leaf in leaves]
    assert len(hashes) == len(set(hashes))
    # No per-work provenance smuggled onto the shared leaf — the body is pure
    # text identity (design §22.1). source_locators / work_id live on the
    # node_version instead, never here.
    for leaf in leaves:
        assert set(leaf.keys()) == {"schema", "text", "content_leaf_hash"}, leaf


def test_content_leaf_hash_is_independent_text_only_recompute() -> None:
    """content_leaf_hash == sha256 of canonical {schema, text} — principled, not circular."""
    import hashlib

    from lawvm.substrate.canonical_json import canonical_json_bytes, nfc

    known_text = "Pysyvä teksti ä — sama jokaisessa työssä."
    clh, body = exporter_mod._content_leaf_body(known_text)

    expected = (
        "sha256:"
        + hashlib.sha256(
            b"lawvm:content_leaf\x00"
            + canonical_json_bytes(
                {"schema": "lawvm.content_leaf.v1", "text": nfc(known_text)}
            )
        ).hexdigest()
    )
    assert clh == expected
    assert body == {
        "schema": "lawvm.content_leaf.v1",
        "text": nfc(known_text),
        "content_leaf_hash": expected,
    }


def test_content_leaf_dedups_across_two_different_works() -> None:
    """THE dedup property (would FAIL under the source_locators-in-leaf bug).

    Two distinct works carry per-work source spans, but identical leaf TEXT must
    yield the byte-identical content leaf — same ``content_leaf_hash`` AND same
    wrapped-row ``object_hash`` — so a shared store deduplicates it. The fix
    moves ``source_locators`` to the node_version; if they rode on the leaf, the
    two works' leaves would hash differently and this assertion would fail.
    """
    from lawvm.substrate.canonical_json import wrap_row

    shared_text = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2000."
    clh_a, body_a = exporter_mod._content_leaf_body(shared_text)
    clh_b, body_b = exporter_mod._content_leaf_body(shared_text)

    assert clh_a == clh_b
    assert body_a == body_b
    # The wire-level dedup key (object_hash) must also match across works.
    assert wrap_row(body_a)["object_hash"] == wrap_row(body_b)["object_hash"]

    # And the per-occurrence node_version DOES distinguish the two works via its
    # source_locators (provenance is preserved, just relocated off the leaf).
    _, nv_a = exporter_mod._node_version_body(
        "sha256:struct", clh_a, ("2000-01-01", None), "permanent", "genesis:x",
        ["farchive:fi:work:1/2000"],
    )
    _, nv_b = exporter_mod._node_version_body(
        "sha256:struct", clh_b, ("2000-01-01", None), "permanent", "genesis:x",
        ["farchive:fi:work:2/2000"],
    )
    assert nv_a["source_locators"] == ["farchive:fi:work:1/2000"]
    assert nv_b["source_locators"] == ["farchive:fi:work:2/2000"]
    # node_version_id is identity-stable (locators excluded from the id tuple)…
    assert nv_a["node_version_id"] == nv_b["node_version_id"]
    # …but the wrapped object_hash differs (locators ARE a visible body member).
    assert wrap_row(nv_a)["object_hash"] != wrap_row(nv_b)["object_hash"]


def test_no_dense_keys_anywhere(synthetic_pack: Path) -> None:
    """No active_at / display_nodes / per-date duplicated dense escape (gotcha 3)."""
    forbidden = {"active_at", "display_nodes", "active_node", "display_node"}
    for jsonl in synthetic_pack.rglob("*.jsonl"):
        for row in _read_layer(jsonl):
            _assert_no_forbidden_keys(row, forbidden, jsonl)
    manifest = json.loads((synthetic_pack / "manifest.json").read_text())
    _assert_no_forbidden_keys(manifest, forbidden, synthetic_pack / "manifest.json")
    # No SQLite db smuggled in.
    assert not list(synthetic_pack.rglob("*.db"))
    assert not list(synthetic_pack.rglob("*.sqlite"))


def test_hash_prefix_and_ensure_ascii(synthetic_pack: Path) -> None:
    """Every object_hash is single-prefixed; on-disk bytes are ASCII-escaped."""
    for jsonl in synthetic_pack.rglob("*.jsonl"):
        raw = jsonl.read_bytes()
        # ensure_ascii: the non-ASCII ä must be unicode-escaped, never raw UTF-8.
        assert "ä".encode("utf-8") not in raw, f"raw non-ASCII bytes in {jsonl}"
        for row in _read_layer(jsonl):
            oh = row["object_hash"]
            assert oh.startswith("sha256:"), oh
            assert not oh.startswith("sha256:sha256:"), f"double prefix: {oh}"


def test_universe_keystone_present(synthetic_pack: Path) -> None:
    """The selection universe + at least one selected row exist (omission keystone)."""
    state_rows = _read_layer(synthetic_pack / "state" / "state.jsonl")
    universes = [
        r for r in state_rows if r["object"].get("schema") == "lawvm.selection_universe.v1"
    ]
    rows = [r for r in state_rows if r["object"].get("schema") == "lawvm.selection_row.v1"]
    assert len(universes) == 1
    assert len(rows) >= 1
    # Every selection row carries an explicit selection_key (checker L0.6 contract).
    for r in rows:
        assert isinstance(r["object"].get("selection_key"), str)


@pytest.mark.slow
def test_e2e_real_statute(tmp_path: Path) -> None:
    """End-to-end replay of a real FI statute → pack → VALID (slow; needs farchive)."""
    farchive = Path("data/finlex.farchive")
    if not farchive.exists():
        pytest.skip("finlex farchive not reachable")
    out = tmp_path / "pack_real"
    result = export_work_pack("301/2004", out, jurisdiction="fi", quiet=True)
    assert result.n_selection_rows > 0
    pack = load_pack_for_check(out)
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    assert verdict.integrity in (
        IntegrityVerdict.VALID,
        IntegrityVerdict.VALID_WITH_UNSUPPORTED_LAYERS,
    ), [v.to_canonical_dict() for v in verdict.violations]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
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


def test_exporter_module_has_no_sqlite_import() -> None:
    """The exporter must not depend on sqlite3 (the dense escape hatch)."""
    src = Path(exporter_mod.__file__).read_text(encoding="utf-8")
    assert "import sqlite3" not in src
