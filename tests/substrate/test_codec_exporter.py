"""Exporter integration for the storage codec: address stability + on-disk framing.

The codec sits BENEATH the content address. Exporting the same work under
``identity`` and ``zstd`` must produce:

* byte-identical per-layer ``root`` and ``uncompressed_sha256`` (the content
  address never moves — OBJECT_MODEL §3 three-hash split);
* a DIFFERENT ``storage_sha256`` per layer (the blob framing changed);
* an EXACT round-trip on read (``load_pack_for_check`` decodes the ``.zst`` blob
  back to the canonical bytes and both packs verify VALID_CLEAN);
* a strictly smaller on-disk footprint for the redundant JSONL layers.

Uses the same synthetic adapter as ``test_exporter.py`` so it runs in ms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.substrate import codec
from lawvm.substrate.checker import CheckMode, IntegrityVerdict, TopLineVerdict, check_pack
from lawvm.substrate.exporter import export_work_pack, load_pack_for_check


# --------------------------------------------------------------------------- #
# Synthetic adapter (mirrors test_exporter.py)                                 #
# --------------------------------------------------------------------------- #


class _FakeProfile:
    def canonical_statute_id(self, raw: str) -> str:
        a, b = raw.split("/")
        if len(a) == 4:
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
        title="Testilaki ä ö",
        result=None,
        lo_ops=[],
        timelines={},
        change_dates=["2000-01-01", "2005-01-01", "2010-01-01"],
        replay_findings=[],
        failed_ops=[],
        source_pathologies=[],
    )


def _section(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind("section"), label=label, text=text)


def _tree_for_date(_bundle: Any, date: str) -> IRNode:
    children = [_section("1", "Pysyvä teksti ä" * 40)]  # big + repeated -> compresses
    if date < "2005-01-01":
        children.append(_section("2", "Alkuperäinen kakkospykälä" * 40))
    else:
        children.append(_section("2", "Muutettu kakkospykälä" * 40))
    if date >= "2010-01-01":
        children.append(_section("3", "Uusi kolmospykälä" * 40))
    return IRNode(kind=IRNodeKind("body"), label=None, text="", children=tuple(children))


class _FakeAdapter:
    def __init__(self) -> None:
        self.profile = _FakeProfile()
        self.tree_materializer = _tree_for_date

    def replay_runner(self, _engine_id: str, *, profile: Any) -> Any:
        return _fake_bundle()


@pytest.fixture()
def _patched_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _FakeAdapter()
    monkeypatch.setattr(
        "lawvm.tools.transition_graph_jurisdictions.transition_graph_adapter_for_jurisdiction",
        lambda _j: adapter,
    )


def _export(out: Path, storage_codec: str) -> Path:
    export_work_pack("1/2000", out, jurisdiction="fi", quiet=True, storage_codec=storage_codec)
    return out


def _layer_by_kind(pack: Any) -> dict[str, Any]:
    return {layer.kind: layer for layer in pack.manifest.layers}


# --------------------------------------------------------------------------- #
# Identity path is unchanged (default)                                         #
# --------------------------------------------------------------------------- #


def test_identity_default_writes_plain_jsonl(_patched_adapter: None, tmp_path: Path) -> None:
    out = _export(tmp_path / "ident", codec.IDENTITY_CODEC)
    assert (out / "base" / "base.jsonl").exists()
    assert not (out / "base" / "base.jsonl.zst").exists()
    pack = load_pack_for_check(out)
    for layer in pack.manifest.layers:
        assert layer.codec == codec.IDENTITY_CODEC
        # Identity: storage == uncompressed.
        assert layer.storage_sha256 == layer.uncompressed_sha256


def test_identity_pack_still_valid_clean(_patched_adapter: None, tmp_path: Path) -> None:
    out = _export(tmp_path / "ident", codec.IDENTITY_CODEC)
    verdict = check_pack(load_pack_for_check(out), mode=CheckMode.AUDIT)
    assert verdict.integrity is IntegrityVerdict.VALID
    assert verdict.top_line_verdict is TopLineVerdict.VALID_CLEAN


# --------------------------------------------------------------------------- #
# zstd path                                                                    #
# --------------------------------------------------------------------------- #

zstd_only = pytest.mark.skipif(
    not codec.zstd_available(), reason="zstandard library not installed"
)


@zstd_only
def test_zstd_writes_zst_blobs_not_plaintext(_patched_adapter: None, tmp_path: Path) -> None:
    out = _export(tmp_path / "zst", codec.ZSTD_CODEC)
    # The .zst blob is on disk; the plaintext .jsonl is NOT.
    assert (out / "base" / "base.jsonl.zst").exists()
    assert not (out / "base" / "base.jsonl").exists()
    pack = load_pack_for_check(out)
    for layer in pack.manifest.layers:
        assert layer.codec == codec.ZSTD_CODEC
        assert layer.path.endswith(".zst")


@zstd_only
def test_zstd_pack_valid_clean_via_roundtrip(_patched_adapter: None, tmp_path: Path) -> None:
    # load_pack_for_check must decode the blobs back to canonical bytes; if the
    # round-trip were lossy the layer-root recompute in the checker would fail.
    out = _export(tmp_path / "zst", codec.ZSTD_CODEC)
    verdict = check_pack(load_pack_for_check(out), mode=CheckMode.AUDIT)
    assert verdict.integrity is IntegrityVerdict.VALID, [
        v.to_canonical_dict() for v in verdict.violations
    ]
    assert verdict.top_line_verdict is TopLineVerdict.VALID_CLEAN


@zstd_only
def test_address_identical_under_identity_and_zstd(_patched_adapter: None, tmp_path: Path) -> None:
    ident = load_pack_for_check(_export(tmp_path / "ident", codec.IDENTITY_CODEC))
    zst = load_pack_for_check(_export(tmp_path / "zst", codec.ZSTD_CODEC))

    li = _layer_by_kind(ident)
    lz = _layer_by_kind(zst)
    assert set(li) == set(lz)
    for kind in li:
        # Content address (layer root) is byte-identical: the codec does not move it.
        assert li[kind].root == lz[kind].root, kind
        # Canonical-byte digest is byte-identical too.
        assert li[kind].uncompressed_sha256 == lz[kind].uncompressed_sha256, kind
        # But the on-disk blob framing changed → storage hash differs (and the
        # identity storage hash equals its uncompressed hash by definition).
        assert li[kind].storage_sha256 == li[kind].uncompressed_sha256, kind
        assert lz[kind].storage_sha256 != lz[kind].uncompressed_sha256, kind

    # Every per-row object_hash is identical across the two packs (the address of
    # each stored object is codec-independent).
    for kind in li:
        rows_i = ident.layers[kind].rows
        rows_z = zst.layers[kind].rows
        assert [r["object_hash"] for r in rows_i] == [r["object_hash"] for r in rows_z], kind


@zstd_only
def test_zstd_shrinks_on_disk_footprint(_patched_adapter: None, tmp_path: Path) -> None:
    ident = _export(tmp_path / "ident", codec.IDENTITY_CODEC)
    zst = _export(tmp_path / "zst", codec.ZSTD_CODEC)

    def layer_bytes(root: Path) -> int:
        plain = sum(f.stat().st_size for f in root.rglob("*.jsonl"))
        packed = sum(f.stat().st_size for f in root.rglob("*.jsonl.zst"))
        return plain + packed

    ident_bytes = layer_bytes(ident)
    zst_bytes = layer_bytes(zst)
    assert zst_bytes < ident_bytes
