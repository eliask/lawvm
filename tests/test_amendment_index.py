from __future__ import annotations

from pathlib import Path

from lawvm.corpus_store import CorpusStore
from lawvm.finland.amendment_index import build_amendment_index, ensure_amendment_index


class _FakeCorpus(CorpusStore):
    def __init__(self, *, oracle_map: dict[str, bytes], source_map: dict[str, bytes]) -> None:
        self._oracle_map = oracle_map
        self._source_map = source_map

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

    edges = build_amendment_index(cs=corpus)

    assert ("2024/1049", "1986/506", "source_vts_explicit") in edges
    assert ("1991/806", "1986/506", "oracle_amendedBy") in edges


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


def test_ensure_amendment_index_rebuilds_old_two_column_schema(tmp_path: Path) -> None:
    csv_path = tmp_path / "amendment_parents.csv"
    csv_path.write_text("amendment_id,parent_id\n1991/806,1986/506\n", encoding="utf-8")
    corpus = _FakeCorpus(oracle_map={}, source_map={})

    ensure_amendment_index(cs=corpus, csv_path=csv_path)

    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "amendment_id,parent_id,edge_kind"
