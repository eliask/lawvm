from __future__ import annotations
from typing_extensions import override

from contextlib import contextmanager
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

    @override
    def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
        return {sid: f"oracle://{sid}" for sid in self._oracle_map}

    @override
    def read_oracle(self, sid: str) -> bytes | None:
        return self._oracle_map.get(sid)

    @override
    def read_source(self, sid: str) -> bytes | None:
        return self._source_map.get(sid)

    @override
    def list_statute_ids(self) -> list[str]:
        return sorted(self._source_map)

    @override
    def close(self) -> None:
        return None

    @override
    def read_media(self, sid: str, filename: str) -> bytes | None:
        return None

    @override
    def read_corrigendum_media(self, sid: str, filename: str) -> bytes | None:
        return None

    @override
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


def test_build_amendment_index_supplements_dated_title_cross_statute_vts_edges() -> None:
    oracle_xml = b"""
    <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification>
          <FRBRWork>
            <FRBRdate date="1901-04-23" name="dateIssued"/>
          </FRBRWork>
        </identification>
      </meta>
      <preface><docTitle>Laki kuolleeksi julistamisesta</docTitle></preface>
    </act>
    """
    source_xml = """
    <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <body>
        <hcontainer eId="entryIntoForce" name="entryIntoForce">
          <content>
            <p>2. Kumottavat säännökset. Tällä lailla kumotaan avioliittolain
            voimaanpanosta 13 päivänä kesäkuuta 1929 annetun lain (235/29) 10,
            11 ja 15§, rikoslain 19 luvun 6 § sekä kuolleeksi julistamisesta
            23 päivänä huhtikuuta 1901 annetun lain 15 §, sellaisena kuin se on
            23 päivänä toukokuuta 1975 annetussa laissa (351/75).</p>
          </content>
        </hcontainer>
      </body>
    </act>
    """.encode("utf-8")
    corpus = _FakeCorpus(
        oracle_map={"1901/15-001": oracle_xml},
        source_map={"1987/411": source_xml},
    )

    diagnostics: list[dict[str, object]] = []

    edges = build_amendment_index(cs=corpus, diagnostics_out=diagnostics)

    assert ("1987/411", "1901/15-001", "source_vts_explicit") in edges
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


def _amendedby_oracle(amend_id: str) -> bytes:
    year, num = amend_id.split("/")
    return (
        '<act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<meta><references>"
        f'<amendedBy><ref href="/akn/fi/act/statute/{year}/{num}"/></amendedBy>'
        "</references></meta></act>"
    ).encode("utf-8")


def _enacting_clause_oracle(clause_text: str) -> bytes:
    return (
        '<act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<preface>"
        '<formula name="enactingClause"><p>'
        f"{clause_text}"
        "</p></formula>"
        "</preface></act>"
    ).encode("utf-8")


def test_oracle_edge_dropped_when_johtolause_cites_other_statute() -> None:
    # Parent 1901/15-001 claims 1987/411 amended it, but 1987/411's johtolause
    # names a different statute (avioliittolaki 234/29). The spurious edge must
    # be dropped while the correctly-cited parent 1929/234 keeps the edge.
    diagnostics: list[dict[str, object]] = []
    corpus = _FakeCorpus(
        oracle_map={
            "1901/15-001": _amendedby_oracle("1987/411"),
            "1929/234": _amendedby_oracle("1987/411"),
            "1987/411": _enacting_clause_oracle(
                "Eduskunnan päätöksen mukaisesti kumotaan 13 päivänä kesäkuuta "
                "1929 annetun avioliittolain (234/29) 55 § seuraavasti:"
            ),
        },
        source_map={},
    )

    edges = build_amendment_index(cs=corpus, diagnostics_out=diagnostics)

    assert ("1987/411", "1929/234", "oracle_amendedBy") in edges
    assert ("1987/411", "1901/15-001", "oracle_amendedBy") not in edges
    rejected = [
        d
        for d in diagnostics
        if d["rule_id"] == "fi_amendment_index_oracle_edge_rejected_by_johtolause"
    ]
    assert len(rejected) == 1
    assert rejected[0]["amendment_id"] == "1987/411"
    assert rejected[0]["parent_id"] == "1901/15-001"
    assert rejected[0]["johtolause_cited_parents"] == ["1929/234"]


def test_oracle_edge_kept_when_johtolause_is_sparse() -> None:
    # A fresh act whose enacting clause names no statute (only "säädetään:")
    # must keep its oracle edge — the clause being uninformative is not a
    # contradiction, so a legitimate amendment is never dropped.
    corpus = _FakeCorpus(
        oracle_map={
            "2021/612": _amendedby_oracle("2023/741"),
            "2023/741": _enacting_clause_oracle(
                "Eduskunnan päätöksen mukaisesti säädetään:"
            ),
        },
        source_map={},
    )

    edges = build_amendment_index(cs=corpus)

    assert ("2023/741", "2021/612", "oracle_amendedBy") in edges


def test_oracle_edge_kept_when_cited_numbers_are_uncorroborated_provenance() -> None:
    # A malformed provenance clause ("sellaisna kuin" — a real typo for
    # "sellaisina") can make the routing surface mistake prior-amendment numbers
    # for targets. The parent here is named by date/name only and the "cited"
    # numbers are not oracle candidate parents of the amendment, so the edge must
    # be KEPT rather than dropping a legitimate amendment.
    corpus = _FakeCorpus(
        oracle_map={
            "1929/234": _amendedby_oracle("1983/362"),
            "1983/362": _enacting_clause_oracle(
                "muutetaan 13 päivänä kesäkuuta 1929 annetun avioliittolain 80 §, "
                "sellaisna kuin se on 23 päivänä syyskuuta 1948 annetussa laissa "
                "(681/48) ja 23 päivänä toukokuuta 1975 annetussa laissa (705/75)"
            ),
        },
        source_map={},
    )

    edges = build_amendment_index(cs=corpus)

    assert ("1983/362", "1929/234", "oracle_amendedBy") in edges


def test_oracle_edges_kept_for_legitimate_multi_target_amendment() -> None:
    # One amendment validly amending several statutes cites each of them; every
    # cited target keeps its edge, and a non-cited parent that wrongly claims the
    # amendment is dropped.
    clause = (
        "Eduskunnan päätöksen mukaisesti muutetaan ensimmäisen lain (111/2000) 1 § "
        "sekä toisen lain (222/2001) 2 § seuraavasti:"
    )
    corpus = _FakeCorpus(
        oracle_map={
            "2000/111": _amendedby_oracle("2010/500"),
            "2001/222": _amendedby_oracle("2010/500"),
            "2099/999": _amendedby_oracle("2010/500"),
            "2010/500": _enacting_clause_oracle(clause),
        },
        source_map={},
    )

    edges = build_amendment_index(cs=corpus)

    assert ("2010/500", "2000/111", "oracle_amendedBy") in edges
    assert ("2010/500", "2001/222", "oracle_amendedBy") in edges
    assert ("2010/500", "2099/999", "oracle_amendedBy") not in edges


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


def test_extract_explicit_cross_statute_vts_parents_records_clause_text_on_vts_extraction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AGENTS.md §1.10 / §2.9: a swallowed ``extract_voimaantulo_repeals``
    exception MUST embed a truncated ``clause_text`` of the source xml_data so
    triaging the residual does not require re-running extraction (the typed
    rebuttal to silently-defensive defaults). Covers both
    ``_append_amendment_index_diagnostic`` call sites at the
    ``fi_amendment_index_source_vts_parent_extraction_failed`` rule_id.
    """
    source_xml = (
        '<act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<body>"
        '<hcontainer eId="entryIntoForce" name="entryIntoForce">'
        "<content><p>Tämä laki kumotaan asetus (506/1986).</p></content>"
        "</hcontainer>"
        "</body></act>"
    ).encode("utf-8")

    class _ProbeVtsFailure(RuntimeError):
        pass

    def _raise_on_extract(*args: object, **kwargs: object) -> bool:
        raise _ProbeVtsFailure("synthetic extract_voimaantulo_repeals failure")

    monkeypatch.setattr(
        amendment_index, "extract_voimaantulo_repeals", _raise_on_extract
    )

    diagnostics: list[dict[str, object]] = []
    candidates = amendment_index._extract_explicit_cross_statute_vts_parents(
        source_xml,
        "2024/123",
        diagnostics_out=diagnostics,
    )

    # Sanity: the extraction returned no parents (the exception was caught).
    assert candidates == set()
    # Both call sites fired: the first loop over ``cited_ids`` discovered
    # ``(506/1986)`` and the second loop is untouched because no dated
    # parent-title candidates are passed in. The single diagnostic emitted is
    # the ``extract_voimaantulo_repeals`` failure.
    assert [item["rule_id"] for item in diagnostics] == [
        "fi_amendment_index_source_vts_parent_extraction_failed"
    ]
    diagnostic = diagnostics[0]
    # §1.10: ``clause_text`` embeds the verbatim source xml_data ~400 chars.
    assert diagnostic["clause_text"] == source_xml.decode("utf-8", errors="replace")
    # The marker fires only when the source exceeds the ~400-char bound; the
    # negative guard (no truncation marker on a short source) is asserted
    # here too, mirroring ``test_truncate_repr_helper_short_value_is_unchanged``
    # in ``tests/test_typed_carrier_protocols.py``.
    assert "…[truncated]" not in str(diagnostic["clause_text"])
    # Per-site fields stay inline (not absorbed into ``clause_text``): the
    # parent_id (normalized to ``YEAR/NUMBER`` form by
    # ``_normalize_source_citation_id``) and exception_type identify the root
    # cause without re-running.
    assert diagnostic["parent_id"] == "1986/506"
    assert diagnostic["exception_type"] == "_ProbeVtsFailure"


def test_extract_explicit_cross_statute_vts_parents_clause_text_truncates_long_source_xml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AGENTS.md §1.10: ``clause_text`` MUST be bounded to ~400 chars with a
    truncation marker when the source xml_data exceeds the bound — mirrors the
    ``core.named_swallow._truncate_clause_text`` precedent; mirrors
    ``test_unregistered_claim_assertion_truncates_large_value_repr`` in
    ``tests/test_typed_carrier_protocols.py``.
    """
    # Build a >400-char source XML that retains a citation the VTS extractor
    # recognises (so the exception path fires): the citation is what routes
    # into ``extract_voimaantulo_repeals``; the long filler exercises the
    # truncation ceiling.
    long_filler = "etuä " * 200  # ~1000 chars
    source_xml = (
        '<act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<body>"
        '<hcontainer eId="entryIntoForce" name="entryIntoForce">'
        f"<content><p>Tämä laki kumotaan asetus (506/1986). {long_filler}</p></content>"
        "</hcontainer>"
        "</body></act>"
    ).encode("utf-8")

    class _ProbeVtsFailure(RuntimeError):
        pass

    def _raise_on_extract(*args: object, **kwargs: object) -> bool:
        raise _ProbeVtsFailure("synthetic extract_voimaantulo_repeals failure")

    monkeypatch.setattr(
        amendment_index, "extract_voimaantulo_repeals", _raise_on_extract
    )

    diagnostics: list[dict[str, object]] = []
    amendment_index._extract_explicit_cross_statute_vts_parents(
        source_xml,
        "2024/123",
        diagnostics_out=diagnostics,
    )

    assert len(diagnostics) == 1
    clause_text = str(diagnostics[0]["clause_text"])
    # The truncation marker is present and the payload is bounded to the §1.10
    # ceiling + the marker suffix (mirrors ``_truncate_xml_clause_text``).
    assert "…[truncated]" in clause_text
    assert len(clause_text) <= amendment_index._XML_CLAUSE_TEXT_MAX_CHARS + len(
        "…[truncated]"
    )
    # The full unbounded source MUST NOT fit in the clause_text slot.
    assert source_xml.decode("utf-8", errors="replace") not in clause_text


def test_ensure_amendment_index_rebuilds_old_two_column_schema(tmp_path: Path) -> None:
    csv_path = tmp_path / "amendment_parents.csv"
    csv_path.write_text("amendment_id,parent_id\n1991/806,1986/506\n", encoding="utf-8")
    corpus = _FakeCorpus(oracle_map={}, source_map={})

    ensure_amendment_index(cs=corpus, csv_path=csv_path)

    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(amendment_index._CSV_HEADER)


def test_default_amendment_index_cache_uses_canonical_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LAWVM_FINLAND_AMENDMENT_INDEX_CACHE", raising=False)
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))

    assert amendment_index._default_cache_csv() == (
        tmp_path / ".cache" / "finland" / "amendment_parents.csv"
    )


def test_default_amendment_index_cache_env_override_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override = tmp_path / "custom" / "parents.csv"
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path / "canonical"))
    monkeypatch.setenv("LAWVM_FINLAND_AMENDMENT_INDEX_CACHE", str(override))

    assert amendment_index._default_cache_csv() == override


def test_amendment_index_cache_writes_use_per_writer_temp_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = tmp_path / "amendment_parents.csv"
    replaced: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def record_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        replaced.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(amendment_index.os, "replace", record_replace)

    amendment_index._write_amendment_index_cache(
        csv_path,
        [("1991/806", "1986/506", "oracle_amendedBy")],
        source_fingerprint=None,
    )

    assert [dst for _src, dst in replaced] == [csv_path, csv_path.with_suffix(".meta.json")]
    assert all(src.name.endswith(".tmp") for src, _dst in replaced)
    assert all(src.name != f".{dst.name}.tmp" for src, dst in replaced)


def test_ensure_amendment_index_adopts_current_schema_csv_when_meta_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = tmp_path / "amendment_parents.csv"
    meta_path = csv_path.with_suffix(".meta.json")
    db_path = tmp_path / "finlex.farchive"
    csv_path.write_text(
        ",".join(amendment_index._CSV_HEADER)
        + "\n1991/806,1986/506,oracle_amendedBy,source_vts_title_date_v2\n",
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
    assert meta["schema"] == amendment_index._CSV_HEADER
    assert meta["source"] == amendment_index._corpus_source_fingerprint(corpus)


def test_ensure_amendment_index_rechecks_cache_after_acquiring_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = tmp_path / "amendment_parents.csv"
    db_path = tmp_path / "finlex.farchive"
    db_path.write_bytes(b"current")
    corpus = _FakeCorpus(
        oracle_map={},
        source_map={},
        archive=_FakeArchive(db_path),
    )
    source_fingerprint = amendment_index._corpus_source_fingerprint(corpus)

    @contextmanager
    def populate_cache_before_yield(_csv_path: Path):
        amendment_index._write_amendment_index_cache(
            csv_path,
            [("1991/806", "1986/506", "oracle_amendedBy")],
            source_fingerprint,
        )
        yield

    def fail_build(*args: object, **kwargs: object) -> list[tuple[str, str, str]]:
        raise AssertionError("cache populated by another worker should be reused")

    monkeypatch.setattr(amendment_index, "_amendment_index_cache_lock", populate_cache_before_yield)
    monkeypatch.setattr(amendment_index, "build_amendment_index", fail_build)

    ensure_amendment_index(cs=corpus, csv_path=csv_path)

    assert csv_path.exists()
    assert csv_path.with_suffix(".meta.json").exists()


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
        ",".join(amendment_index._CSV_HEADER)
        + "\n1991/806,1986/506,oracle_amendedBy,source_vts_title_date_v2\n",
        encoding="utf-8",
    )
    meta_path.write_text(
        json.dumps(
            {
                "schema": amendment_index._CSV_HEADER,
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
    assert (
        "2026/269,2011/805,oracle_amendedBy,source_vts_title_date_v2"
        in csv_path.read_text(encoding="utf-8")
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["source"] == amendment_index._corpus_source_fingerprint(corpus)


def test_build_amendment_index_reads_oracles_via_index_locators() -> None:
    oracle_xml = b"""
    <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <references>
          <amendedBy><ref href="/akn/fi/act/statute/1991/806"/></amendedBy>
        </references>
      </meta>
    </act>
    """

    class _NoPerSidOracleCorpus(_FakeCorpus):
        @override
        def read_locator(self, locator: str) -> bytes | None:
            if locator.startswith("oracle://"):
                return self._oracle_map.get(locator.removeprefix("oracle://"))
            return None

        @override
        def read_oracle(self, sid: str) -> bytes | None:
            raise AssertionError(
                "per-sid read_oracle() re-scans the locator table; the index "
                "build must read via oracle_path_index() locators"
            )

    corpus = _NoPerSidOracleCorpus(
        oracle_map={"1986/506": oracle_xml},
        source_map={},
    )

    edges = build_amendment_index(cs=corpus)

    assert ("1991/806", "1986/506", "oracle_amendedBy") in edges


def test_ensure_amendment_index_tolerates_path_representation_change(
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
        ",".join(amendment_index._CSV_HEADER)
        + "\n1991/806,1986/506,oracle_amendedBy,source_vts_title_date_v2\n",
        encoding="utf-8",
    )
    fingerprint = amendment_index._corpus_source_fingerprint(corpus)
    assert fingerprint is not None
    # Same archive recorded under a different path spelling (e.g. a meta
    # written before path resolution became canonical, or via a symlink).
    monkeypatch.chdir(tmp_path)
    stored = dict(fingerprint)
    stored["path"] = "finlex.farchive"
    meta_path.write_text(
        json.dumps(
            {
                "schema": amendment_index._CSV_HEADER,
                "source": stored,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_build(*args: object, **kwargs: object) -> list[tuple[str, str, str]]:
        raise AssertionError("path representation change must not force a rebuild")

    monkeypatch.setattr(amendment_index, "build_amendment_index", fail_build)

    ensure_amendment_index(cs=corpus, csv_path=csv_path)


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
        ",".join(amendment_index._CSV_HEADER)
        + "\n1991/806,1986/506,oracle_amendedBy,source_vts_title_date_v2\n",
        encoding="utf-8",
    )
    meta_path.write_text(
        json.dumps(
            {
                "schema": amendment_index._CSV_HEADER,
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
