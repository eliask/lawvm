"""Multi-statute synergy gate (PROTOTYPE_PLAN_V0.md §16) — the four proofs.

Pins for :mod:`lawvm.substrate.corpus`: the smallest test that a per-work pack
is a graph **node**, not an island. Each test asserts a real measurement /
artifact:

(a) content-leaf dedup across works resolves by IDENTICAL hash + the shared
    ``base/`` store saves the duplicated bytes;
(b) the manifest's ``storage_codec`` / ``dict_id`` (+ per-layer + reserved
    ``dict/``) give a shared-dictionary seam with no schema change;
(c) a real ``lawvm.overlay.v1`` cross-work resolution (content-addressed
    ``resolution_id``) goes into ``edges/<corpus_version>`` and ``check-pack``
    accepts it (and a tamper of it is caught — the layer is genuinely checked);
(d) one work's ``selection_universe`` root is byte-identical regardless of
    the other work (per-work roots compose without cross-contamination).

The fixtures are hand-built synthetic content-leaf packs (no replay), so the
gate runs fast and offline; the real-statute end-to-end equivalents live in
``test_exporter.py`` (slow) and the session artifacts under ``.tmp/``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from lawvm.substrate.canonical_json import JsonValue, wrap_row
from lawvm.substrate.checker import (
    CheckMode,
    IntegrityVerdict,
    TopLineVerdict,
    ViolationCode,
    check_pack,
)
from lawvm.substrate.corpus import (
    WorkAnchor,
    build_corpus_pack,
    make_cross_work_resolution,
    measure_leaf_dedup,
    shared_dict_seam,
    universe_root_of,
)
from lawvm.substrate.exporter import load_pack_for_check
from lawvm.substrate.manifest import PackLayer, PackManifest, PackProvenance
from lawvm.substrate.roots import map_root, set_root

CV = "fi:test:corpus:2000-01-01"

# A leaf SHARED by both synthetic works (identical text → identical hash) plus
# per-work distinct leaves. This is the positive control for cross-work dedup.
_SHARED_TEXT = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2000."


def _leaf_body(text: str) -> dict[str, JsonValue]:
    from lawvm.substrate.roots import leaf_hash

    # PURE text identity — the body is {schema, text, content_leaf_hash} and
    # NOTHING per-work (no source_locators, no work_id), exactly as the exporter
    # now emits. This is what makes the shared leaf byte-identical across the two
    # synthetic works (the cross-work dedup property, design §22.1).
    body: dict[str, JsonValue] = {
        "schema": "lawvm.content_leaf.v1",
        "text": text,
    }
    # content_leaf_hash = leaf_hash over the body without the hash field (matches
    # the exporter's _content_leaf_body discipline).
    clh = leaf_hash("content_leaf", body)
    body["content_leaf_hash"] = clh
    return body


def _write_work_base_pack(out: Path, work_id: str, distinct_texts: list[str]) -> Path:
    """Write a tiny single-work pack carrying only a ``base/`` content-leaf store.

    Includes ``_SHARED_TEXT`` plus ``distinct_texts``; a ``work`` header row so
    the corpus builder can read ``work_id``. ``state/trace/proof`` are absent
    (the checker tolerates a base-only browse pack).
    """
    base_dir = out / "base"
    base_dir.mkdir(parents=True, exist_ok=True)
    base_path = base_dir / "base.jsonl"

    rows: list[dict[str, JsonValue]] = []
    work_body: dict[str, JsonValue] = {
        "schema": "lawvm.work.v1",
        "work_id": work_id,
        "title": work_id,
        "corpus_version": CV,
    }
    rows.append(wrap_row(work_body))
    for text in [_SHARED_TEXT, *distinct_texts]:
        rows.append(wrap_row(_leaf_body(text)))

    # Dedup by object_hash (the shared leaf must appear once even if listed twice).
    seen: dict[str, dict[str, JsonValue]] = {}
    for row in rows:
        seen[str(row["object_hash"])] = row
    ordered = [seen[h] for h in sorted(seen)]
    with base_path.open("w", encoding="utf-8") as fh:
        for row in ordered:
            fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            fh.write("\n")

    base_root = set_root("base", [str(r["object_hash"]) for r in ordered])
    digest = _sha256(base_path)
    manifest = PackManifest(
        pack_kind="lawvm.pack.work.v0",
        work_ids=(work_id,),
        corpus_version=CV,
        identity_encoding="lawvm.canonical_json.v1",
        storage_codec="identity",
        dict_id="",
        profiles=("lawvm.canon.semantic_text.v1",),
        selection_profiles=("lawvm.selection.governing_text.v1",),
        schemas={"content_leaf": "lawvm.content_leaf.v1", "work": "lawvm.work.v1"},
        layers=(
            PackLayer(
                kind="base",
                path="base/base.jsonl",
                row_schema="lawvm.layer.base.v0",
                codec="identity",
                dict_id="",
                uncompressed_sha256=digest,
                storage_sha256=digest,
                root=base_root,
                root_fn="SetRoot",
                row_count=len(ordered),
            ),
        ),
        roots={"base_root": base_root},
        required_layers_for_browse=("base",),
        required_layers_for_audit=("base",),
        optional_layers=("edges", "dict"),
        provenance=_prov(),
    )
    (out / "manifest.json").write_text(
        json.dumps(
            wrap_row(manifest.to_canonical_dict()),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return out


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(path.read_bytes())
    return "sha256:" + h.hexdigest()


def _prov() -> PackProvenance:
    return PackProvenance(
        lawvm_git_commit="test",
        engine_version="test",
        source_policy_id="archival_exact",
        checkable_source_bundle_policy="archival_exact",
        created_at="2000-01-01T00:00:00+00:00",
        dirty_tree=False,
    )


@pytest.fixture()
def two_work_packs(tmp_path: Path) -> tuple[Path, Path]:
    a = _write_work_base_pack(tmp_path / "A", "fi:act:1/2000", ["A-only provision alpha", "A-only beta"])
    b = _write_work_base_pack(tmp_path / "B", "fi:act:2/2000", ["B-only provision gamma"])
    return a, b


# --------------------------------------------------------------------------- #
# (a) content-leaf dedup across works                                          #
# --------------------------------------------------------------------------- #


def test_a_shared_leaf_resolves_by_identical_hash(two_work_packs: tuple[Path, Path]) -> None:
    a, b = two_work_packs
    report = measure_leaf_dedup({"A": a, "B": b})
    # Exactly the one shared leaf, agreeing on its content_leaf_hash across packs.
    assert report.n_shared == 1, report.summary()
    assert report.hash_consistency_ok is True
    # A shared store saves exactly the duplicated shared-leaf bytes.
    assert report.saved_bytes > 0
    assert report.shared_store_bytes < report.independent_total_bytes


def test_a_distinct_works_have_zero_false_dedup(tmp_path: Path) -> None:
    """Two works with NO shared text share zero leaves (no spurious collisions)."""
    a = _write_work_base_pack(tmp_path / "A", "fi:act:1/2000", ["only-A"])
    b = _write_work_base_pack(tmp_path / "B", "fi:act:2/2000", ["only-B"])
    # Remove the shared leaf from B by rewriting B without _SHARED_TEXT.
    base = (b / "base" / "base.jsonl")
    kept = [
        line
        for line in base.read_text(encoding="utf-8").splitlines()
        if _SHARED_TEXT not in json.loads(line)["object"].get("text", "")
    ]
    base.write_text("\n".join(kept) + "\n", encoding="utf-8")
    report = measure_leaf_dedup({"A": a, "B": b})
    assert report.n_shared == 0, report.summary()


# --------------------------------------------------------------------------- #
# (b) shared zstd frame / dictionary seam                                      #
# --------------------------------------------------------------------------- #


def test_b_shared_dict_seam_fields(two_work_packs: tuple[Path, Path]) -> None:
    a, _ = two_work_packs
    seam = shared_dict_seam(a)
    # v0 ships uncompressed identity; the seam fields exist for a later shared dict.
    assert seam["manifest.storage_codec"] == "identity"
    assert seam["manifest.dict_id"] == ""
    assert seam["per_layer.codec"]["base"] == "identity"
    assert seam["per_layer.dict_id"]["base"] == ""
    # identity codec ⇒ storage hash == uncompressed hash (no transform yet).
    assert seam["per_layer.storage_sha256_vs_uncompressed"]["base"] is True
    # the reserved dict/ layer is declared optional (the dictionary store seam).
    assert seam["dict_layer_reserved_optional"] is True


# --------------------------------------------------------------------------- #
# (c) cross-work resolution in edges/                                          #
# --------------------------------------------------------------------------- #


def test_c_resolution_is_content_addressed_and_targets_work_b(
    two_work_packs: tuple[Path, Path],
) -> None:
    source = WorkAnchor("fi:act:1/2000", "sha256:" + "a" * 64, "chapter:1/section:1")
    target = WorkAnchor("fi:act:2/2000", "sha256:" + "b" * 64, "chapter:1/section:7")
    res = make_cross_work_resolution(
        source=source, target=target, surface_expr_text="2/2000 7 §:ssä", corpus_version=CV
    )
    assert res["schema"] == "lawvm.overlay.v1"
    assert res["overlay_kind"] == "reference_resolution"
    rid = res["resolution_id"]
    assert isinstance(rid, str) and rid.startswith("sha256:")
    # resolution_id is a function of content — re-deriving with the same inputs
    # gives the same id; changing the target changes it (never positional).
    res2 = make_cross_work_resolution(
        source=source, target=target, surface_expr_text="2/2000 7 §:ssä", corpus_version=CV
    )
    assert res2["resolution_id"] == rid
    target_b = WorkAnchor("fi:act:2/2000", "sha256:" + "c" * 64, "chapter:1/section:9")
    res3 = make_cross_work_resolution(
        source=source, target=target_b, surface_expr_text="2/2000 7 §:ssä", corpus_version=CV
    )
    assert res3["resolution_id"] != rid
    # The resolution points across works (A → B).
    selector = res["target_selector"]
    assert isinstance(selector, Mapping)
    selector_map = cast("Mapping[str, JsonValue]", selector)
    assert selector_map["target_work_id"] == "fi:act:2/2000"


def test_c_corpus_pack_with_edges_is_accepted(two_work_packs: tuple[Path, Path]) -> None:
    a, b = two_work_packs
    source = WorkAnchor("fi:act:1/2000", "sha256:" + "a" * 64, "chapter:1/section:1")
    target = WorkAnchor("fi:act:2/2000", "sha256:" + "b" * 64, "chapter:1/section:7")
    res = make_cross_work_resolution(
        source=source, target=target, surface_expr_text="2/2000 7 §:ssä", corpus_version=CV
    )
    out = a.parent / "corpus"
    result = build_corpus_pack(
        member_pack_dirs={"A": a, "B": b}, out_dir=out, resolutions=[res], corpus_version=CV
    )
    assert result.n_edges == 1
    # Shared base store = union (the shared leaf appears once across both works).
    # A: shared + 2 distinct = 3; B: shared + 1 distinct = 2; union = 4.
    assert result.n_shared_base_leaves == 4

    pack = load_pack_for_check(out)
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    # The edges overlay schema is unknown but the edges layer is optional, so the
    # pack is valid-with-unsupported-layers (the seam is open, nothing weakened).
    assert verdict.integrity is IntegrityVerdict.VALID_WITH_UNSUPPORTED_LAYERS, [
        v.to_canonical_dict() for v in verdict.violations
    ]
    assert verdict.violations == ()
    assert "edges" in verdict.unsupported_layers


def test_c_edges_layer_is_genuinely_checked(two_work_packs: tuple[Path, Path]) -> None:
    """A tampered resolution body (stale object_hash) is caught → INVALID_HASH."""
    a, b = two_work_packs
    source = WorkAnchor("fi:act:1/2000", "sha256:" + "a" * 64, "chapter:1/section:1")
    target = WorkAnchor("fi:act:2/2000", "sha256:" + "b" * 64, "chapter:1/section:7")
    res = make_cross_work_resolution(
        source=source, target=target, surface_expr_text="2/2000 7 §:ssä", corpus_version=CV
    )
    out = a.parent / "corpus_tamper"
    build_corpus_pack(
        member_pack_dirs={"A": a, "B": b}, out_dir=out, resolutions=[res], corpus_version=CV
    )
    edges_files = list((out / "edges").rglob("edges.jsonl"))
    assert len(edges_files) == 1
    rows = [json.loads(line) for line in edges_files[0].read_text().splitlines() if line.strip()]
    rows[0]["object"]["resolution_status"] = "broken"  # mutate body, leave object_hash stale
    edges_files[0].write_text(
        json.dumps(rows[0], ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    verdict = check_pack(load_pack_for_check(out), mode=CheckMode.BROWSE)
    assert verdict.top_line_verdict is TopLineVerdict.INVALID_HASH
    assert verdict.has_code(ViolationCode.INVALID_HASH)


# --------------------------------------------------------------------------- #
# (d) selection_universe_root stability                                        #
# --------------------------------------------------------------------------- #


def test_d_universe_root_is_independent_of_other_works(tmp_path: Path) -> None:
    """A work's selection_universe identity does not change when bundled with another.

    Built from synthetic state rows: the universe object hash + selection_key_root
    are a pure function of work A's own selection keys, so re-reading them in a
    context where work B exists yields byte-identical roots.
    """
    # Two independent contexts for the SAME work A's universe: one alone, one
    # alongside an unrelated work B in the same parent dir.
    a_alone = _write_state_only_pack(tmp_path / "alone", "fi:act:1/2000", ["k1", "k2", "k3"])
    _ = _write_work_base_pack(tmp_path / "with_b" / "B", "fi:act:2/2000", ["b-only"])
    a_with_b = _write_state_only_pack(
        tmp_path / "with_b" / "A", "fi:act:1/2000", ["k1", "k2", "k3"]
    )

    u_alone = universe_root_of(a_alone)
    u_with_b = universe_root_of(a_with_b)
    assert u_alone.universe_object_hash == u_with_b.universe_object_hash
    assert u_alone.selection_key_root == u_with_b.selection_key_root
    # And the key root changes if A's OWN keys change (sanity: it is not constant).
    a_changed = _write_state_only_pack(tmp_path / "changed", "fi:act:1/2000", ["k1", "k2"])
    assert universe_root_of(a_changed).selection_key_root != u_alone.selection_key_root


def _write_state_only_pack(out: Path, work_id: str, keys: list[str]) -> Path:
    """Write a pack whose state layer carries a selection_universe over ``keys``.

    The universe's ``selection_key_root`` is a MapRoot over the keys (the omission
    keystone) — derived purely from work A's own keys, so it is corpus-invariant.
    """
    state_dir = out / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    key_to_hash = {k: set_root("selection_key", [k]) for k in keys}
    selection_key_root = map_root("selection_key", key_to_hash)
    universe_body: dict[str, JsonValue] = {
        "schema": "lawvm.selection_universe.v1",
        "work_id": work_id,
        "selection_key_root": selection_key_root,
        "branch_ids": ["actual"],
        "query_profile_ids": ["lawvm.selection.governing_text.v1"],
    }
    row = wrap_row(universe_body)
    (state_dir / "state.jsonl").write_text(
        json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return out
