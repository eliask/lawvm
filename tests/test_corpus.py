from __future__ import annotations

import datetime as dt
from typing import Any, cast

import lxml.etree as etree

from lawvm import corpus_store as shared_corpus
from lawvm.finland import corpus
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.corpus import _oracle_pending_amendment_suspect


class _FakeArchive:
    def __init__(self, mapping: dict[str, bytes]) -> None:
        self._mapping = mapping

    def get(self, path: str):
        return self._mapping.get(path)


class _FakeCorpus:
    def __init__(self, oracle_paths: dict[str, str], blobs: dict[str, bytes]) -> None:
        self._oracle_paths = oracle_paths
        self._archive = _FakeArchive(blobs)
        self.selector_calls: list[object] = []

    def oracle_path_index(self, **kwargs: object):
        self.selector_calls.append(kwargs.get("selector"))
        return dict(self._oracle_paths)

    def read_locator(self, locator: str):
        return self._archive.get(locator)

    def read_oracle(self, sid: str):
        locator = self._oracle_paths.get(sid)
        if locator is None:
            return None
        return self._archive.get(locator)

    def read_source(self, statute_id: str):
        return self._archive.get(corpus.statute_url(statute_id))


class _FakeArchiveStore:
    def __init__(self, locator_map: dict[str, list[str]], blobs: dict[str, bytes]) -> None:
        self._locator_map = locator_map
        self._blobs = blobs

    def get(self, url: str) -> bytes | None:
        return self._blobs.get(url)

    def locators(self, pattern: str = "%") -> list[str]:
        return list(self._locator_map.get(pattern, []))

    def fetch(self, url: str, max_age_hours: float | None = None) -> bytes | None:
        return self.get(url)

    def close(self) -> None:
        return None


def _consolidated_xml(*, version: str, date_consolidated: str) -> bytes:
    return f"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRWork>
            <FRBRthis value="/akn/fi/act/statute-consolidated/2014/1429/fin@{version}/!main"/>
          </FRBRWork>
          <FRBRManifestation>
            <FRBRdate date="{date_consolidated}" name="dateConsolidated"/>
          </FRBRManifestation>
        </identification>
      </meta>
      <body><section><num>1 §</num><p>{version}</p></section></body>
    </akn>
    """.encode("utf-8")


def _source_xml(*, effective_date: str) -> bytes:
    return f"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRManifestation>
            <FRBRdate date="{effective_date}" name="dateIssued"/>
          </FRBRManifestation>
        </identification>
      </meta>
      <dateEntryIntoForce date="{effective_date}"/>
    </akn>
    """.encode("utf-8")


def test_cache_only_suspect_flags_base_oracle_with_pre_cutoff_amendment(monkeypatch) -> None:
    sid = "2020/508"
    oracle_path = "akn/fi/act/statute-consolidated/2020/508/fin@/main.xml"
    oracle_xml = b"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRExpression>
            <FRBRdate date="2022-05-20" name="dateConsolidated"/>
          </FRBRExpression>
        </identification>
      </meta>
    </akn>
    """
    amend_xml = b"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta><proprietary><dateEntryIntoForce date="2021-07-01"/></proprietary></meta>
      <dateEntryIntoForce date="2021-07-01"/>
    </akn>
    """
    fake = _FakeCorpus(
        {sid: oracle_path},
        {
            oracle_path: oracle_xml,
            corpus.statute_url("2021/609"): amend_xml,
        },
    )
    monkeypatch.setattr(corpus, "_get_amendment_children_map", lambda: {sid: {"2021/609"}})

    suspect, pending = corpus.get_consolidated_oracle_suspect_cache_only(sid, cast(Any, fake))

    assert pending == ""
    assert suspect == "oracle_missing_version_pin despite amendment 2021/609 eff 2021-07-01 <= cutoff 2022-05-20"


def test_cache_only_suspect_records_unparseable_missing_pin_child(monkeypatch) -> None:
    sid = "2020/508"
    oracle_path = "akn/fi/act/statute-consolidated/2020/508/fin@/main.xml"
    oracle_xml = b"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRExpression>
            <FRBRdate date="2022-05-20" name="dateConsolidated"/>
          </FRBRExpression>
        </identification>
      </meta>
    </akn>
    """
    fake = _FakeCorpus(
        {sid: oracle_path},
        {
            oracle_path: oracle_xml,
            corpus.statute_url("2021/609"): b"<akn>",
        },
    )
    monkeypatch.setattr(corpus, "_get_amendment_children_map", lambda: {sid: {"2021/609"}})

    suspect, pending = corpus.get_consolidated_oracle_suspect_cache_only(sid, cast(Any, fake))

    assert suspect == ""
    assert pending == "oracle_missing_version_pin_amendment_unparseable:2021/609"


def test_cache_only_suspect_prefers_proven_child_over_unparseable_pending(monkeypatch) -> None:
    sid = "2020/508"
    oracle_path = "akn/fi/act/statute-consolidated/2020/508/fin@/main.xml"
    oracle_xml = b"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRExpression>
            <FRBRdate date="2022-05-20" name="dateConsolidated"/>
          </FRBRExpression>
        </identification>
      </meta>
    </akn>
    """
    amend_xml = b"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta><proprietary><dateEntryIntoForce date="2021-07-01"/></proprietary></meta>
      <dateEntryIntoForce date="2021-07-01"/>
    </akn>
    """
    fake = _FakeCorpus(
        {sid: oracle_path},
        {
            oracle_path: oracle_xml,
            corpus.statute_url("2021/1"): b"<akn>",
            corpus.statute_url("2021/609"): amend_xml,
        },
    )
    monkeypatch.setattr(corpus, "_get_amendment_children_map", lambda: {sid: {"2021/1", "2021/609"}})

    suspect, pending = corpus.get_consolidated_oracle_suspect_cache_only(sid, cast(Any, fake))

    assert pending == ""
    assert suspect == "oracle_missing_version_pin despite amendment 2021/609 eff 2021-07-01 <= cutoff 2022-05-20"


def test_cache_only_suspect_flags_expired_temporary_version_mid(monkeypatch) -> None:
    sid = "2006/1096"
    oracle_path = "akn/fi/act/statute-consolidated/2006/1096/fin@20151425/main.xml"
    oracle_xml = b"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRExpression>
            <FRBRdate date="2025-11-28" name="dateConsolidated"/>
          </FRBRExpression>
        </identification>
      </meta>
    </akn>
    """
    amend_xml = """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta><proprietary><dateEntryIntoForce date="2016-01-01"/></proprietary></meta>
      <dateEntryIntoForce date="2016-01-01"/>
      <body>
        <p>Tämä laki tulee voimaan 1 päivänä tammikuuta 2016 ja se on voimassa 31 päivään toukokuuta 2019.</p>
      </body>
    </akn>
    """.encode("utf-8")
    fake = _FakeCorpus(
        {sid: oracle_path},
        {
            oracle_path: oracle_xml,
            corpus.statute_url("2015/1425"): amend_xml,
        },
    )
    monkeypatch.setattr(corpus, "_get_amendment_children_map", lambda: {sid: {"2015/1425"}})

    suspect, pending = corpus.get_consolidated_oracle_suspect_cache_only(sid, cast(Any, fake))

    assert pending == ""
    assert suspect == "2015/1425 expires 2019-05-31 < cutoff 2025-11-28"


def test_live_suspect_flags_future_version_mid_even_for_small_gap(monkeypatch) -> None:
    sid = "2016/258"
    oracle_path = "akn/fi/act/statute-consolidated/2016/258/fin@20211199/main.xml"
    oracle_xml = b"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRExpression>
            <FRBRdate date="2021-12-17" name="dateConsolidated"/>
          </FRBRExpression>
        </identification>
      </meta>
    </akn>
    """
    amend_xml = """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRManifestation>
            <FRBRdate date="2021-12-17" name="dateIssued"/>
          </FRBRManifestation>
        </identification>
      </meta>
      <body>
        <section>
          <num>8 §</num>
          <content>
            <p>T\u00e4m\u00e4 asetus tulee voimaan 1 p\u00e4iv\u00e4n\u00e4 toukokuuta 2016 ja on voimassa vuoden 2023 loppuun.</p>
          </content>
        </section>
        <hcontainer name="entryIntoForce">
          <content>
            <p>T\u00e4m\u00e4 asetus tulee voimaan 31 p\u00e4iv\u00e4n\u00e4 joulukuuta 2021.</p>
          </content>
        </hcontainer>
      </body>
    </akn>
    """.encode("utf-8")
    fake = _FakeCorpus(
        {sid: oracle_path},
        {
            oracle_path: oracle_xml,
            corpus.statute_url("2021/1199"): amend_xml,
        },
    )

    suspect = corpus.get_consolidated_oracle_suspect(sid, cast(Any, fake))

    assert suspect == "2021/1199 eff 2021-12-31 > cutoff 2021-12-17"


def test_oracle_reflected_source_vts_children_detects_explicit_late_ref(monkeypatch) -> None:
    sid = "1986/506"
    oracle_path = "akn/fi/act/statute-consolidated/1986/506/fin@19941264/main.xml"
    oracle_xml = """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRWork>
            <FRBRthis value="/akn/fi/act/statute-consolidated/1986/506/fin@19941264/!main"/>
          </FRBRWork>
          <FRBRManifestation>
            <FRBRdate date="1994-12-16" name="dateConsolidated"/>
          </FRBRManifestation>
        </identification>
      </meta>
      <preface>
        <block name="noteAuthorial">
          <p>Tämä asetus on jätetty voimaan 2 §:ää lukuun ottamatta. Ks. L <ref href="/akn/fi/act/statute/2024/1049">1049/2024</ref>.</p>
        </block>
      </preface>
    </akn>
    """.encode("utf-8")
    fake = _FakeCorpus({sid: oracle_path}, {oracle_path: oracle_xml})
    monkeypatch.setattr(
        corpus,
        "_get_amendment_child_edges_map",
        lambda: {
            sid: [
                ("1994/1264", "oracle_amendedBy"),
                ("2024/1049", "source_vts_explicit"),
            ]
        },
    )

    got = corpus.get_consolidated_oracle_reflected_source_vts_children(sid, cast(Any, fake))

    assert got == {"2024/1049"}


def test_oracle_reflected_source_vts_children_ignores_unindexed_or_non_vts_refs(monkeypatch) -> None:
    sid = "1986/506"
    oracle_path = "akn/fi/act/statute-consolidated/1986/506/fin@19941264/main.xml"
    oracle_xml = """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRWork>
            <FRBRthis value="/akn/fi/act/statute-consolidated/1986/506/fin@19941264/!main"/>
          </FRBRWork>
          <FRBRManifestation>
            <FRBRdate date="1994-12-16" name="dateConsolidated"/>
          </FRBRManifestation>
        </identification>
      </meta>
      <preface>
        <block name="noteAuthorial">
          <p>Ks. L <ref href="/akn/fi/act/statute/2024/1049">1049/2024</ref>.</p>
        </block>
      </preface>
    </akn>
    """.encode("utf-8")
    fake = _FakeCorpus({sid: oracle_path}, {oracle_path: oracle_xml})
    monkeypatch.setattr(
        corpus,
        "_get_amendment_child_edges_map",
        lambda: {
            sid: [
                ("1994/1264", "oracle_amendedBy"),
                ("2024/1049", "oracle_amendedBy"),
            ]
        },
    )

    got = corpus.get_consolidated_oracle_reflected_source_vts_children(sid, cast(Any, fake))

    assert got == set()


# ---------------------------------------------------------------------------
# _oracle_pending_amendment_suspect tests
# ---------------------------------------------------------------------------

_FINLEX_NS = "http://data.finlex.fi/schema/finlex"
_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_PENDING_ORACLE_XML = f"""
<akn xmlns="{_AKN_NS}" xmlns:finlex="{_FINLEX_NS}">
  <meta>
    <finlex:amendedBy>
      <finlex:statuteReference>
        <finlex:ref href="/akn/fi/act/statute/2024/917">917/2024</finlex:ref>
        <finlex:inForce>
          <finlex:dateEntryIntoForce date="2024-12-31">31.12.2024</finlex:dateEntryIntoForce>
        </finlex:inForce>
      </finlex:statuteReference>
    </finlex:amendedBy>
  </meta>
</akn>
""".encode("utf-8")

_WITHIN_CUTOFF_ORACLE_XML = f"""
<akn xmlns="{_AKN_NS}" xmlns:finlex="{_FINLEX_NS}">
  <meta>
    <finlex:amendedBy>
      <finlex:statuteReference>
        <finlex:ref href="/akn/fi/act/statute/2023/500">500/2023</finlex:ref>
        <finlex:inForce>
          <finlex:dateEntryIntoForce date="2024-06-01">1.6.2024</finlex:dateEntryIntoForce>
        </finlex:inForce>
      </finlex:statuteReference>
    </finlex:amendedBy>
  </meta>
</akn>
""".encode("utf-8")


def test_oracle_pending_amendment_suspect_detects_future_entry_in_force() -> None:
    """amendedBy entry with eff date >180 days after cutoff → returns pending suspect string."""
    oracle_tree = etree.fromstring(_PENDING_ORACLE_XML)
    # Gap must exceed 180-day grace period to trigger detection
    cutoff = dt.date(2024, 5, 1)

    result = _oracle_pending_amendment_suspect(oracle_tree, cutoff)

    assert result is not None
    assert "pending: 2024/917" in result
    assert "eff 2024-12-31" in result
    assert "cutoff 2024-05-01" in result


def test_oracle_pending_amendment_suspect_returns_none_when_in_force_within_cutoff() -> None:
    """amendedBy entry with eff date at or before cutoff → returns None."""
    oracle_tree = etree.fromstring(_WITHIN_CUTOFF_ORACLE_XML)
    cutoff = dt.date(2024, 12, 19)

    result = _oracle_pending_amendment_suspect(oracle_tree, cutoff)

    assert result is None


def test_oracle_pending_amendment_suspect_returns_none_for_oracle_with_no_amended_by() -> None:
    """Oracle with no amendedBy block → returns None."""
    oracle_xml = f"""
    <akn xmlns="{_AKN_NS}"><meta></meta></akn>
    """.encode("utf-8")
    oracle_tree = etree.fromstring(oracle_xml)
    cutoff = dt.date(2024, 12, 19)

    result = _oracle_pending_amendment_suspect(oracle_tree, cutoff)

    assert result is None


def test_archive_corpus_store_prefers_versioned_consolidated_locator() -> None:
    sid = "2002/738"
    versioned = shared_corpus.oracle_url(sid, version="20250001")
    unversioned = f"finlex://sd-cons/{sid}/fin/main.xml"

    archive = _FakeArchiveStore(
        {
            f"finlex://sd-cons/{sid}/fin@%/main.xml": [unversioned, versioned],
            "finlex://sd-cons/%/fin@%/main.xml": [unversioned, versioned],
        },
        {
            unversioned: b"unversioned",
            versioned: b"versioned",
        },
    )
    store = shared_corpus.ArchiveCorpusStore(archive)

    assert store.read_oracle(sid) == b"versioned"
    assert store.oracle_path_index() == {sid: versioned}


def test_archive_corpus_store_ignores_unversioned_consolidated_locator() -> None:
    sid = "2002/738"
    unversioned = f"finlex://sd-cons/{sid}/fin/main.xml"

    archive = _FakeArchiveStore(
        {
            f"finlex://sd-cons/{sid}/fin@%/main.xml": [unversioned],
            "finlex://sd-cons/%/fin@%/main.xml": [unversioned],
        },
        {
            unversioned: b"unversioned",
        },
    )
    store = shared_corpus.ArchiveCorpusStore(archive)

    assert store.read_oracle(sid) is None
    assert store.oracle_path_index() == {}


def test_ground_truth_bytes_uses_explicit_selector() -> None:
    sid = "2002/738"
    latest_locator = "finlex://sd-cons/2002/738/fin@20250001/main.xml"
    exact_locator = "finlex://sd-cons/2002/738/fin@20240012/main.xml"
    fake = _FakeCorpus(
        {
            sid: exact_locator,
        },
        {
            latest_locator: b"<akn><body><section><p>latest</p></section></body></akn>",
            exact_locator: b"<akn><body><section><p>exact</p></section></body></akn>",
        },
    )

    data = corpus.get_ground_truth_bytes(
        sid,
        cast(Any, fake),
        selector=ConsolidatedArtifactSelector.exact_embedded_version("20240012"),
    )

    assert data == b"<akn><body><section><p>exact</p></section></body></akn>"
    assert fake.selector_calls[-1] == ConsolidatedArtifactSelector.exact_embedded_version("20240012")


def test_ground_truth_bytes_latest_selector_uses_default_cached_path() -> None:
    sid = "2002/738"
    latest_locator = "finlex://sd-cons/2002/738/fin@20250001/main.xml"
    fake = _FakeCorpus(
        {sid: latest_locator},
        {
            latest_locator: b"<akn><body><section><p>latest</p></section></body></akn>",
        },
    )

    data = corpus.get_ground_truth_bytes(
        sid,
        cast(Any, fake),
        selector=ConsolidatedArtifactSelector.latest_cached_editorial(),
    )

    assert data == b"<akn><body><section><p>latest</p></section></body></akn>"
    assert fake.selector_calls[-1] is None


def test_ground_truth_bytes_bench_selector_uses_direct_selected_artifact() -> None:
    sid = "2014/1429"
    older = "finlex://sd-cons/2014/1429/fin@20240012/main.xml"
    ahead = "finlex://sd-cons/2014/1429/fin@20250001/main.xml"
    archive = _FakeArchiveStore(
        {"finlex://sd-cons/2014/1429/fin@%/main.xml": [ahead, older]},
        {
            ahead: _consolidated_xml(version="20250001", date_consolidated="2024-01-15"),
            older: _consolidated_xml(version="20240012", date_consolidated="2024-01-15"),
            corpus.statute_url("2025/1"): _source_xml(effective_date="2025-02-01"),
            corpus.statute_url("2024/12"): _source_xml(effective_date="2024-01-01"),
        },
    )

    class _DirectSelectorCorpus:
        def __init__(self) -> None:
            self._archive = archive

        def oracle_path_index(self, **kwargs: object):
            raise AssertionError("custom bench selector should not force global oracle index rebuild")

        def read_locator(self, locator: str):
            return archive.get(locator)

    data = corpus.get_ground_truth_bytes(
        sid,
        cast(Any, _DirectSelectorCorpus()),
        selector=ConsolidatedArtifactSelector.bench_comparable(),
    )

    # Option Z with 180-day tolerance (T5-fix, commit a3870eea):
    # 20250001 has effective 2025-02-01, date_consolidated 2024-01-15.
    # Gap ~383 days > 180-day tolerance → rejected. bench_comparable falls back
    # to the older self-comparable 20240012 (effective 2024-01-01, already in
    # force by date_consolidated).
    assert data is not None
    assert b"20240012" in data


def test_ground_truth_tree_uses_explicit_selector() -> None:
    sid = "2002/738"
    exact_locator = "finlex://sd-cons/2002/738/fin@20240012/main.xml"
    fake = _FakeCorpus(
        {sid: exact_locator},
        {
            exact_locator: b"""
            <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
              <body><section><num>1</num><p>exact</p></section></body>
            </akn>
            """,
        },
    )

    tree = corpus.get_ground_truth_tree(
        sid,
        cast(Any, fake),
        selector=ConsolidatedArtifactSelector.exact_embedded_version("20240012"),
    )

    assert tree is not None
    assert "exact" in "".join(cast(Any, tree).itertext())


def test_akn_path_to_url_rejects_unversioned_consolidated_locator() -> None:
    assert (
        shared_corpus.akn_path_to_url(
            "akn/fi/act/statute-consolidated/2014/1429/fin/main.xml",
        )
        is None
    )


def test_get_consolidated_oracle_context_uses_selected_locator_and_embedded_date() -> None:
    sid = "2014/1429"
    locator = "finlex://sd-cons/2014/1429/fin@20190112/main.xml"
    oracle_xml = b"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRWork>
            <FRBRthis value="/akn/fi/act/statute-consolidated/2014/1429/fin@20190112/!main"/>
          </FRBRWork>
          <FRBRManifestation>
            <FRBRdate date="2024-12-19" name="dateConsolidated"/>
          </FRBRManifestation>
        </identification>
      </meta>
    </akn>
    """
    fake = _FakeCorpus({sid: locator}, {locator: oracle_xml})

    ctx = corpus.get_consolidated_oracle_context(sid, cast(Any, fake))

    assert ctx.locator == locator
    assert ctx.cutoff_date == dt.date(2024, 12, 19)
    assert ctx.oracle_version_amendment_id == "2019/112"


def test_get_consolidated_oracle_context_honors_selector() -> None:
    sid = "2014/1429"
    selected_locator = "finlex://sd-cons/2014/1429/fin@20190112/main.xml"
    other_locator = "finlex://sd-cons/2014/1429/fin@20250001/main.xml"
    oracle_xml = b"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRWork>
            <FRBRthis value="/akn/fi/act/statute-consolidated/2014/1429/fin@20190112/!main"/>
          </FRBRWork>
          <FRBRManifestation>
            <FRBRdate date="2024-01-01" name="dateConsolidated"/>
          </FRBRManifestation>
        </identification>
      </meta>
    </akn>
    """

    class _SelectorAwareCorpus(_FakeCorpus):
        def oracle_path_index(self, **kwargs: object):
            selector = kwargs.get("selector")
            if selector == ConsolidatedArtifactSelector.exact_embedded_version("20190112"):
                return {sid: selected_locator}
            return {sid: other_locator}

    fake = _SelectorAwareCorpus(
        {sid: other_locator},
        {
            selected_locator: oracle_xml,
            other_locator: oracle_xml,
        },
    )

    ctx = corpus.get_consolidated_oracle_context(
        sid,
        cast(Any, fake),
        selector=ConsolidatedArtifactSelector.exact_embedded_version("20190112"),
    )

    assert ctx.locator == selected_locator
    assert ctx.oracle_version_amendment_id == "2019/112"


def test_get_consolidated_oracle_inspection_includes_selector_mode() -> None:
    sid = "2014/1429"
    locator = "finlex://sd-cons/2014/1429/fin@20190112/main.xml"
    oracle_xml = b"""
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <identification source="">
          <FRBRWork>
            <FRBRthis value="/akn/fi/act/statute-consolidated/2014/1429/fin@20190112/!main"/>
          </FRBRWork>
          <FRBRManifestation>
            <FRBRdate date="2024-12-19" name="dateConsolidated"/>
          </FRBRManifestation>
        </identification>
      </meta>
    </akn>
    """
    fake = _FakeCorpus({sid: locator}, {locator: oracle_xml})

    inspection = corpus.get_consolidated_oracle_inspection(
        sid,
        cast(Any, fake),
        selector=ConsolidatedArtifactSelector.exact_embedded_version("20190112"),
    )

    assert inspection.locator == locator
    assert inspection.cutoff_date == dt.date(2024, 12, 19)
    assert inspection.oracle_version_amendment_id == "2019/112"
    assert inspection.selector_mode == "exact_embedded_version"
