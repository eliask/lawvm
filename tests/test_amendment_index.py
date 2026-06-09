from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lawvm.corpus_store import CorpusStore
import lawvm.finland.amendment_index as amendment_index
from lawvm.finland.amendment_index import build_amendment_index, ensure_amendment_index


class _FakeArchive:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path


class _FakeCorpus(CorpusStore):
    def __init__(
        self,
        *,
        oracle_map: dict[str, bytes],
        source_map: dict[str, bytes],
        archive: object | None = None,
    ) -> None:
        self._oracle_map = oracle_map
        self._source_map = source_map
        if archive is not None:
            self._archive = archive

    def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
        return {sid: f"oracle://{sid}" for sid in self._oracle_map}

    def read_oracle(self, sid: str) -> bytes | None:
        return self._oracle_map.get(sid)

    def read_source(self, sid: str) -> bytes | None:
        return self._source_map.get(sid)

    def list_statute_ids(self) -> list[str]:
        return sorted(self._source_map)

    def close(self) -> None:
        return None

    def read_media(self, sid: str, filename: str) -> bytes | None:
        return None

    def read_corrigendum_media(self, sid: str, filename: str) -> bytes | None:
        return None

    def read_locator(self, locator: str) -> bytes | None:
        if locator.startswith("oracle://"):
            return self.read_oracle(locator.removeprefix("oracle://"))
        return None


def test_build_amendment_index_supplements_explicit_cross_statute_vts_edges() -> None:
    oracle_xml = b"""
    <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <references>
          <amendedBy><ref href="/akn/fi/act/statute/1991/806"/></amendedBy>
        </references>
      </meta>
    </act>
    """
    source_xml = """
    <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <body>
        <hcontainer eId="entryIntoForce" name="entryIntoForce">
          <content>
            <p>Tämä laki tulee voimaan 1 päivänä tammikuuta 2025.</p>
            <p>Haastemiesasetus (506/1986) jää sen 2 §:ää lukuun ottamatta voimaan tämän lain tullessa voimaan.</p>
          </content>
        </hcontainer>
      </body>
    </act>
    """.encode("utf-8")
    corpus = _FakeCorpus(
        oracle_map={"1986/506": oracle_xml},
        source_map={"2024/1049": source_xml},
    )

    diagnostics: list[dict[str, object]] = []

    edges = build_amendment_index(cs=corpus, diagnostics_out=diagnostics)

    assert ("2024/1049", "1986/506", "source_vts_explicit") in edges
    assert ("1991/806", "1986/506", "oracle_amendedBy") in edges
    assert diagnostics == []


def test_build_amendment_index_ignores_bare_citation_without_vts_effect() -> None:
    source_xml = """
    <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <body>
        <section>
          <subsection>
            <content>
              <p>Tämä laki liittyy asetukseen (506/1986), mutta ei sisällä kumoamista eikä voimaantulopoikkeusta.</p>
            </content>
          </subsection>
        </section>
      </body>
    </act>
    """.encode("utf-8")
    corpus = _FakeCorpus(oracle_map={}, source_map={"2024/1049": source_xml})

    edges = build_amendment_index(cs=corpus)

    assert ("2024/1049", "1986/506", "source_vts_explicit") not in edges


def test_build_amendment_index_records_skipped_source_artifacts() -> None:
    corpus = _FakeCorpus(
        oracle_map={"1986/506": b"<act>"},
        source_map={"2024/1049": b"<act>"},
    )
    diagnostics: list[dict[str, object]] = []

    edges = build_amendment_index(cs=corpus, diagnostics_out=diagnostics)

    assert edges == []
    assert [item["rule_id"] for item in diagnostics] == [
        "fi_amendment_index_oracle_artifact_skipped",
        "fi_amendment_index_source_vts_xml_parse_failed",
    ]
    assert diagnostics[0]["phase"] == "parse"
    assert diagnostics[0]["family"] == "source_pathology"
    assert diagnostics[0]["parent_id"] == "1986/506"
    assert diagnostics[0]["edge_kind"] == "oracle_amendedBy"
    assert diagnostics[0]["blocking"] is True
    assert diagnostics[0]["strict_disposition"] == "block"
    assert diagnostics[0]["quirks_disposition"] == "record"
    assert diagnostics[1]["phase"] == "parse"
    assert diagnostics[1]["family"] == "source_pathology"
    assert diagnostics[1]["amendment_id"] == "2024/1049"
    assert diagnostics[1]["edge_kind"] == "source_vts_explicit"
    assert diagnostics[1]["blocking"] is True
    assert diagnostics[1]["strict_disposition"] == "block"
    assert diagnostics[1]["quirks_disposition"] == "record"


def test_ensure_amendment_index_rebuilds_old_two_column_schema(tmp_path: Path) -> None:
    csv_path = tmp_path / "amendment_parents.csv"
    csv_path.write_text("amendment_id,parent_id\n1991/806,1986/506\n", encoding="utf-8")
    corpus = _FakeCorpus(oracle_map={}, source_map={})

    ensure_amendment_index(cs=corpus, csv_path=csv_path)

    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "amendment_id,parent_id,edge_kind"


def test_ensure_amendment_index_adopts_current_schema_csv_when_meta_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = tmp_path / "amendment_parents.csv"
    meta_path = csv_path.with_suffix(".meta.json")
    db_path = tmp_path / "finlex.farchive"
    csv_path.write_text(
        "amendment_id,parent_id,edge_kind\n1991/806,1986/506,oracle_amendedBy\n",
        encoding="utf-8",
    )
    db_path.write_bytes(b"current")
    corpus = _FakeCorpus(
        oracle_map={},
        source_map={},
        archive=_FakeArchive(db_path),
    )
    calls: list[object] = []

    def fail_build(*args: object, **kwargs: object) -> list[tuple[str, str, str]]:
        calls.append((args, kwargs))
        raise AssertionError("existing current-schema CSV should be adopted without rebuild")

    monkeypatch.setattr(amendment_index, "build_amendment_index", fail_build)

    ensure_amendment_index(cs=corpus, csv_path=csv_path)

    assert calls == []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["schema"] == ["amendment_id", "parent_id", "edge_kind"]
    assert meta["source"] == amendment_index._corpus_source_fingerprint(corpus)


def test_ensure_amendment_index_rebuilds_when_farchive_fingerprint_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = tmp_path / "amendment_parents.csv"
    meta_path = csv_path.with_suffix(".meta.json")
    db_path = tmp_path / "finlex.farchive"
    db_path.write_bytes(b"old")
    corpus = _FakeCorpus(
        oracle_map={},
        source_map={},
        archive=_FakeArchive(db_path),
    )
    old_fingerprint = amendment_index._corpus_source_fingerprint(corpus)
    csv_path.write_text(
        "amendment_id,parent_id,edge_kind\n1991/806,1986/506,oracle_amendedBy\n",
        encoding="utf-8",
    )
    meta_path.write_text(
        json.dumps(
            {
                "schema": ["amendment_id", "parent_id", "edge_kind"],
                "source": old_fingerprint,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    db_path.write_bytes(b"new larger payload")
    os.utime(db_path, None)
    calls: list[object] = []

    def stub_build(*args: object, **kwargs: object) -> list[tuple[str, str, str]]:
        calls.append((args, kwargs))
        return [("2026/269", "2011/805", "oracle_amendedBy")]

    monkeypatch.setattr(amendment_index, "build_amendment_index", stub_build)

    ensure_amendment_index(cs=corpus, csv_path=csv_path)

    assert len(calls) == 1
    assert "2026/269,2011/805,oracle_amendedBy" in csv_path.read_text(encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["source"] == amendment_index._corpus_source_fingerprint(corpus)


def test_ensure_amendment_index_skips_when_farchive_fingerprint_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = tmp_path / "amendment_parents.csv"
    meta_path = csv_path.with_suffix(".meta.json")
    db_path = tmp_path / "finlex.farchive"
    db_path.write_bytes(b"current")
    corpus = _FakeCorpus(
        oracle_map={},
        source_map={},
        archive=_FakeArchive(db_path),
    )
    csv_path.write_text(
        "amendment_id,parent_id,edge_kind\n1991/806,1986/506,oracle_amendedBy\n",
        encoding="utf-8",
    )
    meta_path.write_text(
        json.dumps(
            {
                "schema": ["amendment_id", "parent_id", "edge_kind"],
                "source": amendment_index._corpus_source_fingerprint(corpus),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[object] = []

    def fail_build(*args: object, **kwargs: object) -> list[tuple[str, str, str]]:
        calls.append((args, kwargs))
        raise AssertionError("fresh cache should not rebuild")

    monkeypatch.setattr(amendment_index, "build_amendment_index", fail_build)

    ensure_amendment_index(cs=corpus, csv_path=csv_path)

    assert calls == []
