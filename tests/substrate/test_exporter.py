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
from lawvm.substrate.canonical_json import semantic_hash
from lawvm.substrate.checker import (
    CheckMode,
    IntegrityVerdict,
    TopLineVerdict,
    ViolationCode,
    check_pack,
)
from lawvm.substrate.exporter import export_work_pack, load_pack_for_check
from lawvm.substrate.roots import set_root


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
# FIX-1/FIX-2 hardening fire drills — RE-DERIVED forge adversary.              #
#                                                                              #
# The original drills (test_checker.py) tamper ONE cell WITHOUT re-deriving    #
# the dependent roots — a strawman the layer-root check (L0.3) already caught. #
# These drills re-derive the layer roots the forger MUST rebuild to get past   #
# L0.3, and prove the checker STILL catches them via the roots-of-roots        #
# recompute (FIX-1) and the now-live closure / omission checks (FIX-2). Each   #
# runs through the REAL production lane (on-disk pack → load_pack_for_check →   #
# check_pack), and each is shown to BITE: VALID_CLEAN before the bytes are      #
# tampered, a violation after.                                                  #
# --------------------------------------------------------------------------- #


def _write_layer(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(r, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
            for r in rows
        ),
        encoding="utf-8",
    )


def _load_manifest(pack: Path) -> dict[str, Any]:
    return json.loads((pack / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(pack: Path, manifest_row: dict[str, Any]) -> None:
    (pack / "manifest.json").write_text(
        json.dumps(manifest_row, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _rederive_layer_descriptor_root(
    manifest_body: dict[str, Any], kind: str, rows: list[dict[str, Any]]
) -> None:
    """Rebuild ONLY the layer descriptor root (L0.3) — what a forger must do to
    get past the per-layer root check after editing rows. Leaves manifest.roots
    (the roots-of-roots map, FIX-1's target) untouched."""
    hashes = [str(r["object_hash"]) for r in rows]
    for layer in manifest_body["layers"]:
        if layer["kind"] == kind:
            domain = {"base": "base", "state": "state", "trace": "trace", "proof": "proof"}[kind]
            fn = set_root if layer["root_fn"] == "SetRoot" else None
            assert fn is not None, "drill only re-derives SetRoot layers"
            layer["root"] = fn(domain, hashes)
            layer["row_count"] = len(rows)


def test_baseline_real_pack_is_valid_clean(synthetic_pack: Path) -> None:
    """Sanity anchor for the drills: the untouched real pack is VALID_CLEAN."""
    verdict = check_pack(load_pack_for_check(synthetic_pack), mode=CheckMode.BROWSE)
    assert verdict.top_line_verdict is TopLineVerdict.VALID_CLEAN, verdict.to_canonical_dict()


def test_drill_bogus_manifest_roots_entry(synthetic_pack: Path) -> None:
    """(a) A bogus ``manifest.roots`` entry (Hole A) — the checker never used to
    recompute the roots-of-roots map. FIX-1: now an INVALID_MANIFEST_ROOT."""
    manifest_row = _load_manifest(synthetic_pack)
    body = manifest_row["object"]
    body["roots"]["materialization_root"] = "sha256:BOGUS"
    body["roots"]["certificate_root"] = "sha256:BOGUS"
    _write_manifest(synthetic_pack, manifest_row)

    verdict = check_pack(load_pack_for_check(synthetic_pack), mode=CheckMode.BROWSE)
    assert verdict.top_line_verdict is not TopLineVerdict.VALID_CLEAN, (
        "Hole A NOT closed: a bogus roots-of-roots map still passes VALID_CLEAN"
    )
    assert verdict.has_code(ViolationCode.INVALID_MANIFEST_ROOT)
    subjects = {v.subject for v in verdict.violations if v.code is ViolationCode.INVALID_MANIFEST_ROOT}
    assert {"materialization_root", "certificate_root"} <= subjects


def test_drill_rederived_content_forge(synthetic_pack: Path) -> None:
    """(b) A re-derived content forge: rewrite a content_leaf text, re-wrap the
    row, recompute the BASE layer descriptor root (so L0.3 passes), and rebuild
    the manifest — but the forger does NOT rebuild manifest.roots. FIX-1's
    ``selection_index_root`` recompute (over the forged content_leaf) catches it.
    The forge changes a root the checker now recomputes (verified by asserting
    the violating subject IS selection_index_root)."""
    base_rows = _read_layer(synthetic_pack / "base" / "base.jsonl")
    forged = False
    for row in base_rows:
        if row["object"].get("schema") == "lawvm.content_leaf.v1":
            row["object"]["text"] = "FORGED LAW TEXT"
            # Re-wrap: a real forger recomputes the object_hash so L0.2 passes.
            row["object_hash"] = semantic_hash(row["object"])
            forged = True
            break
    assert forged, "no content_leaf to forge"
    _write_layer(synthetic_pack / "base" / "base.jsonl", base_rows)

    manifest_row = _load_manifest(synthetic_pack)
    body = manifest_row["object"]
    _rederive_layer_descriptor_root(body, "base", base_rows)  # forger passes L0.3
    _write_manifest(synthetic_pack, manifest_row)

    verdict = check_pack(load_pack_for_check(synthetic_pack), mode=CheckMode.BROWSE)
    assert verdict.top_line_verdict is not TopLineVerdict.VALID_CLEAN, (
        "Hole B NOT closed: a re-derived content forge still passes VALID_CLEAN"
    )
    assert verdict.has_code(ViolationCode.INVALID_MANIFEST_ROOT)
    subjects = {v.subject for v in verdict.violations if v.code is ViolationCode.INVALID_MANIFEST_ROOT}
    # The forged content propagates into content_leaf_root → selection_index_root,
    # which the forger did NOT rebuild in manifest.roots.
    assert "selection_index_root" in subjects, subjects


def test_drill_referential_closure_break(synthetic_pack: Path) -> None:
    """(c) Drop a content_leaf row so a node_version→content_leaf ref dangles.
    FIX-2 makes L0.5 live (referenced_hashes populated from real refs)."""
    base_rows = _read_layer(synthetic_pack / "base" / "base.jsonl")
    kept = [r for r in base_rows if r["object"].get("schema") != "lawvm.content_leaf.v1"]
    assert len(kept) < len(base_rows), "expected at least one content_leaf to drop"
    _write_layer(synthetic_pack / "base" / "base.jsonl", kept)

    manifest_row = _load_manifest(synthetic_pack)
    body = manifest_row["object"]
    _rederive_layer_descriptor_root(body, "base", kept)  # forger rebuilds L0.3 root
    _write_manifest(synthetic_pack, manifest_row)

    verdict = check_pack(load_pack_for_check(synthetic_pack), mode=CheckMode.BROWSE)
    assert verdict.top_line_verdict is not TopLineVerdict.VALID_CLEAN, (
        "FIX-2 closure NOT live: a dropped content leaf still passes VALID_CLEAN"
    )
    assert verdict.has_code(ViolationCode.INVALID_MISSING_OBJECT) or verdict.has_code(
        ViolationCode.INVALID_MANIFEST_ROOT
    )


def test_drill_shrunken_universe(synthetic_pack: Path) -> None:
    """(d) Remove a selection_row (shrink the universe). FIX-2 takes the universe
    MapRoot from the COMMITTED universe row, so present!=declared now fires
    (omission honesty was dead when the root was rebuilt from present rows)."""
    state_rows = _read_layer(synthetic_pack / "state" / "state.jsonl")
    rows = [r for r in state_rows if r["object"].get("schema") == "lawvm.selection_row.v1"]
    assert len(rows) >= 1
    drop_hash = rows[0]["object_hash"]
    kept = [r for r in state_rows if r["object_hash"] != drop_hash]
    _write_layer(synthetic_pack / "state" / "state.jsonl", kept)

    manifest_row = _load_manifest(synthetic_pack)
    body = manifest_row["object"]
    _rederive_layer_descriptor_root(body, "state", kept)  # forger rebuilds L0.3 root
    _write_manifest(synthetic_pack, manifest_row)

    verdict = check_pack(load_pack_for_check(synthetic_pack), mode=CheckMode.BROWSE)
    assert verdict.top_line_verdict is not TopLineVerdict.VALID_CLEAN, (
        "FIX-2 omission honesty NOT live: a dropped selection row still passes"
    )
    # The committed universe MapRoot no longer matches the present rows, and the
    # selection_index_root (over the shrunken selection_row family) shifts too.
    assert verdict.has_code(ViolationCode.INVALID_ROOT) or verdict.has_code(
        ViolationCode.INVALID_MANIFEST_ROOT
    )


def test_drill_tampered_cert_layer(synthetic_pack: Path) -> None:
    """(e) FIX-3 partial: tamper cert/certificate.json's committed root. The cert
    layer used to be never read; now it is re-rooted and a disagreement bites."""
    cert_file = synthetic_pack / "cert" / "certificate.json"
    cert_row = json.loads(cert_file.read_text(encoding="utf-8"))
    cert_row["object"]["materialization_root"] = "sha256:BOGUS_CERT"
    # Re-wrap so the cert row's own object_hash is self-consistent (a real forger
    # would; the bite must come from re-rooting, not the wrapper hash).
    cert_row["object_hash"] = semantic_hash(cert_row["object"])
    cert_file.write_text(
        json.dumps(cert_row, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    verdict = check_pack(load_pack_for_check(synthetic_pack), mode=CheckMode.BROWSE)
    assert verdict.top_line_verdict is not TopLineVerdict.VALID_CLEAN, (
        "FIX-3 cert re-root NOT live: a tampered cert layer still passes"
    )
    assert verdict.has_code(ViolationCode.INVALID_MANIFEST_ROOT)


# --------------------------------------------------------------------------- #
# Faithfulness regression drills (audited exporter bugs Q6 / Q4 / blocking)    #
#                                                                              #
# Each asserts the load-bearing invariant the audit found violated. They are   #
# written so they would FAIL against the pre-fix exporter:                     #
#   * Q6 determinism — pre-fix corpus_version = date.today() → pack_id differs  #
#     across days; post-fix the SAME input → the SAME pack_id on any day.       #
#   * Q4 no-silent-drop — pre-fix the message-only projection collapsed N       #
#     distinct pathologies to a handful of identical bodies the proof SetRoot   #
#     deduped away while coverage still reported N; post-fix N distinct → N      #
#     distinct residual objects AND coverage residual == emitted-object count.  #
#   * blocking — pre-fix hardcoded False; post-fix read from the object's own   #
#     field so a blocking residual surfaces.                                    #
# --------------------------------------------------------------------------- #


class _FakePathology:
    """A synthetic source pathology mirroring the engine's ``SourcePathology``.

    Carries its distinguishing identity ONLY on ``as_detail()`` (``code``,
    ``amendment_id``, ``blocking``, …) — exactly like the real object, where
    ``blocking`` is a detail field, not a Python attribute. The message is shared
    across instances so the pre-fix message-only projection would collapse them.
    """

    _SHARED_MESSAGE = "amendment source artifact missing"

    def __init__(self, amendment_id: str, *, blocking: bool = False) -> None:
        self._amendment_id = amendment_id
        self._blocking = blocking
        self.message = self._SHARED_MESSAGE  # identical across instances

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": "fi_amendment_selection_source_artifact_missing",
            "message": self._SHARED_MESSAGE,
            "amendment_id": self._amendment_id,
            "phase": "acquisition",
            "blocking": self._blocking,
            "strict_disposition": "record",
            "target_unit_kind": "",
        }


class _PathologyAdapter(_FakeAdapter):
    """A fake adapter whose replay bundle carries the given source pathologies."""

    def __init__(self, pathologies: list[Any]) -> None:
        super().__init__()
        self._pathologies = pathologies

    def replay_runner(self, _engine_id: str, *, profile: Any) -> Any:
        import dataclasses as _dc

        return _dc.replace(
            super().replay_runner(_engine_id, profile=profile),
            source_pathologies=self._pathologies,
        )


def _adapter_with_pathologies(pathologies: list[Any]) -> _FakeAdapter:
    return _PathologyAdapter(pathologies)


def _export_with_adapter(
    adapter: _FakeAdapter, out: Path, monkeypatch: pytest.MonkeyPatch
) -> Any:
    monkeypatch.setattr(
        "lawvm.tools.transition_graph_jurisdictions.transition_graph_adapter_for_jurisdiction",
        lambda _j: adapter,
    )
    return export_work_pack("1/2000", out, jurisdiction="fi", quiet=True)


def test_q6_pack_id_deterministic_across_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SAME engine input must yield the SAME pack_id on different days.

    Pre-fix ``corpus_version`` was ``date.today()`` — a wall-clock value that
    flowed into the hashed manifest + every account_interval, so the pack_id
    drifted day to day (defeating content-addressing). This pins two distinct
    mocked dates to the SAME identical input and requires an identical pack_id.
    """
    import datetime as _dt

    class _FakeDate(_dt.date):
        _fake = _dt.date(2024, 1, 1)

        @classmethod
        def today(cls) -> _dt.date:  # type: ignore[override]
            return cls._fake

    def _export_on(fakedate: str, out: Path) -> str:
        _FakeDate._fake = _dt.date.fromisoformat(fakedate)
        monkeypatch.setattr(exporter_mod._dt, "date", _FakeDate)
        return _export_with_adapter(_FakeAdapter(), out, monkeypatch).pack_id

    pack_id_day1 = _export_on("2024-01-01", tmp_path / "day1")
    pack_id_day2 = _export_on("2025-12-31", tmp_path / "day2")
    assert pack_id_day1 == pack_id_day2, (
        f"pack_id is wall-clock dependent: {pack_id_day1} != {pack_id_day2}"
    )
    # And the deterministic corpus_version carries no calendar date.
    manifest = _load_manifest(tmp_path / "day1")["object"]
    assert _dt.date.today().isoformat() not in manifest["corpus_version"]


def test_q4_distinct_pathologies_not_silently_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N distinct pathologies → N distinct residual objects; coverage == count.

    Pre-fix: distinct pathologies (different ``amendment_id``, same message)
    projected to identical residual bodies that the proof SetRoot deduped to ONE,
    while the coverage row still reported N. This requires (a) N distinct residual
    OBJECTS on disk and (b) the coverage ``residual`` count to EQUAL that — no
    internal divergence.
    """
    n = 12
    pathologies = [_FakePathology(f"19{i:02d}/{i}") for i in range(n)]
    _export_with_adapter(_adapter_with_pathologies(pathologies), tmp_path / "p", monkeypatch)

    proof_rows = _read_layer(tmp_path / "p" / "proof" / "proof.jsonl")
    residual_objs = [
        r for r in proof_rows if r["object"].get("schema") == "lawvm.residual.v1"
    ]
    distinct_hashes = {r["object_hash"] for r in residual_objs}
    assert len(distinct_hashes) == n, (
        f"silent drop: {n} distinct pathologies collapsed to {len(distinct_hashes)} objects"
    )
    # Every residual carries its distinguishing identity (amendment_id).
    amendment_ids = {
        r["object"]["detail_fields"].get("amendment_id") for r in residual_objs
    }
    assert len(amendment_ids) == n

    coverage = [
        r["object"]
        for r in proof_rows
        if r["object"].get("schema") == "lawvm.coverage_row.v1"
        and r["object"].get("coverage_class") == "residual"
    ]
    assert len(coverage) == 1
    assert coverage[0]["count"] == len(residual_objs), (
        "coverage residual count diverges from emitted residual object count"
    )


def test_blocking_pathology_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocking source-pathology must carry ``blocking: True`` (never hardcoded).

    Pre-fix the exporter hardcoded ``blocking=False`` for every pathology, so a
    blocking source pathology was silently demoted out of the certification fold.
    """
    pathologies = [
        _FakePathology("2001/1", blocking=False),
        _FakePathology("2002/2", blocking=True),
    ]
    _export_with_adapter(_adapter_with_pathologies(pathologies), tmp_path / "b", monkeypatch)

    proof_rows = _read_layer(tmp_path / "b" / "proof" / "proof.jsonl")
    by_amendment = {
        r["object"]["detail_fields"]["amendment_id"]: r["object"]["blocking"]
        for r in proof_rows
        if r["object"].get("schema") == "lawvm.residual.v1"
    }
    assert by_amendment == {"2001/1": False, "2002/2": True}


def test_pack_with_pathologies_still_valid_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legitimately-exported pack with residuals still passes check-pack.

    Guards the critical consistency constraint: the hardened checker recomputes
    the manifest roots over the loaded rows; the richer residual bodies must not
    break that round-trip (integrity stays VALID).
    """
    pathologies = [_FakePathology(f"190{i}/{i}") for i in range(5)]
    out = tmp_path / "v"
    _export_with_adapter(_adapter_with_pathologies(pathologies), out, monkeypatch)
    verdict = check_pack(load_pack_for_check(out), mode=CheckMode.BROWSE)
    assert verdict.integrity is IntegrityVerdict.VALID, [
        v.to_canonical_dict() for v in verdict.violations
    ]


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
