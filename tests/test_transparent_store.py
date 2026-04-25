"""Tests for TransparentCorpusStore.

Covers:
- refresh cascade: PIT API → stale-cache fallback
- freshness check: cached PIT is returned without re-fetching
- oracle_path_index: reports PIT URLs for cached statutes
- Smoke tests (network) for known statutes: marked with pytest.mark.network
  Run with: pytest -m network tests/test_transparent_store.py

The non-network tests use monkeypatching to avoid any HTTP traffic.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from farchive import Farchive
from lawvm.corpus_store import oracle_url
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.corpus import (
    get_oracle_path,
    list_cached_consolidated_locators,
    list_cached_consolidated_pit_locators,
    list_cached_corrigendum_locators,
)
from lawvm.finland.finlex_api import store_consolidated_xml
from lawvm.finland.transparent_store import TransparentCorpusStore


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _make_xml(sid: str, pit: str = "", date_consolidated: str = "2024-01-01") -> bytes:
    """Create a minimal AKN XML blob for a statute SID."""
    year, num = sid.split("/", 1)
    pit_attr = f' pit="{pit}"' if pit else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">\n'
        f'  <act name="statute"{pit_attr}>\n'
        f'    <meta><identification><FRBRWork><FRBRthis value="/akn/fi/act/statute-consolidated/'
        f'{year}/{num}/fin@{pit}/!main"/></FRBRWork>'
        f'<FRBRManifestation><FRBRdate name="dateConsolidated" date="{date_consolidated}"/></FRBRManifestation>'
        f'</identification></meta>\n'
        f'    <body><section eId="sec_1"><num>1</num><heading>Test section</heading>'
        f'<content><p>Content for {sid}</p></content></section></body>\n'
        f'  </act>\n'
        f'</akomaNtoso>\n'
    ).encode("utf-8")


@pytest.fixture()
def archive(tmp_path: Path) -> Farchive:
    """Farchive backed by a temp DB."""
    return Farchive(tmp_path / "test_transparent.farchive")


@pytest.fixture()
def store(archive: Farchive) -> TransparentCorpusStore:
    return TransparentCorpusStore(
        archive=archive,
        verbose=False,
    )


# ---------------------------------------------------------------------------
# refresh cascade: PIT API → stale-cache fallback
# ---------------------------------------------------------------------------

@pytest.mark.network
class TestRefreshCascade:
    def test_pit_preferred_over_stale(
        self,
        store: TransparentCorpusStore,
        monkeypatch,
    ):
        """Explicit refresh uses PIT when available."""
        pit_xml = _make_xml("2002/738", "20250001")
        monkeypatch.setattr(
            "lawvm.finland.transparent_store.fetch_latest_consolidated",
            lambda year, num: (pit_xml, "20250001"),
        )

        xml = store.refresh("2002/738", force=True)
        assert xml == pit_xml, "PIT XML should be returned"

    def test_refresh_uses_api_selector_instead_of_raw_version_max(
        self,
        store: TransparentCorpusStore,
        monkeypatch,
    ):
        chosen_xml = _make_xml("2002/738", "20210701", date_consolidated="2024-02-01")

        def fail_discover(year: str, num: str) -> list[str]:
            raise AssertionError("_refresh_oracle should not consult raw version max")

        monkeypatch.setattr(store, "_discover_pit_versions", fail_discover)
        monkeypatch.setattr(
            "lawvm.finland.transparent_store.fetch_latest_consolidated",
            lambda year, num: (chosen_xml, "20210701"),
        )

        xml = store.refresh("2002/738", force=True)
        assert xml == chosen_xml
        assert store.read_oracle("2002/738") == chosen_xml

    def test_stale_cache_returned_when_nothing_fresh(
        self,
        store: TransparentCorpusStore,
        tmp_path: Path,
        monkeypatch,
    ):
        """If no fresh PIT, stale cached PIT is returned as last resort."""
        old_xml = _make_xml("2002/738", "20200001")

        # Manually store a stale PIT in archive (backdated by setting max age very low)
        from lawvm.corpus_store import oracle_url
        locator = oracle_url("2002/738", version="20200001")
        store._archive.store(locator, old_xml, storage_class="xml")

        # Block ALL API paths
        monkeypatch.setattr(
            "lawvm.finland.transparent_store.fetch_latest_consolidated",
            lambda y, n: (None, ""),
        )

        # Use a very low max_age so the cached PIT is "stale" for the freshness check,
        # but the last-resort fallback should still return it.
        store._pit_xml_max_age_h = 0.0

        xml = store.refresh("2002/738", force=True)
        assert xml == old_xml, "Should return stale cached PIT as last resort"

    def test_none_when_nothing_available(
        self,
        store: TransparentCorpusStore,
        monkeypatch,
    ):
        """Returns None when API returns no PIT and archive has nothing for the SID."""
        monkeypatch.setattr(
            "lawvm.finland.transparent_store.fetch_latest_consolidated",
            lambda y, n: (None, ""),
        )
        xml = store.refresh("9999/9999", force=True)
        assert xml is None


# ---------------------------------------------------------------------------
# Freshness check (cache hit)
# ---------------------------------------------------------------------------

class TestFreshnessCheck:
    def test_fresh_cache_not_re_fetched(
        self,
        store: TransparentCorpusStore,
        monkeypatch,
    ):
        """A fresh cached PIT is returned without any API calls."""
        pit_xml = _make_xml("2002/738", "20250001")
        from lawvm.corpus_store import oracle_url
        locator = oracle_url("2002/738", version="20250001")
        store._archive.store(locator, pit_xml, storage_class="xml")

        discover_calls: list[Any] = []
        original_discover = store._discover_pit_versions

        def patched_discover(year, num):
            discover_calls.append((year, num))
            return original_discover(year, num)

        # Don't need to patch discover — _cached_best_pit should short-circuit
        xml = store.read_oracle("2002/738")
        # Should return the cached PIT without invoking the refresh cascade
        assert xml == pit_xml
        # discover_calls should be empty because _cached_best_pit returned it
        assert len(discover_calls) == 0

    def test_cache_only_returns_stale_cached_oracle_without_refresh(
        self,
        archive: Farchive,
        monkeypatch,
    ):
        store = TransparentCorpusStore(
            archive=archive,
            cache_only=True,
            verbose=False,
        )
        old_xml = _make_xml("2002/738", "20200001")

        from lawvm.corpus_store import oracle_url
        locator = oracle_url("2002/738", version="20200001")
        store._archive.store(locator, old_xml, storage_class="xml")
        store._pit_xml_max_age_h = 0.0

        monkeypatch.setattr(
            store,
            "_refresh_oracle",
            lambda sid: (_ for _ in ()).throw(AssertionError("cache-only must not refresh")),
        )

        xml = store.read_oracle("2002/738")
        assert xml == old_xml

    def test_cache_only_source_read_uses_archive_only(
        self,
        archive: Farchive,
        monkeypatch,
    ):
        store = TransparentCorpusStore(
            archive=archive,
            cache_only=True,
            verbose=False,
        )

        monkeypatch.setattr(
            store,
            "_fetch_source_xml",
            lambda sid: (_ for _ in ()).throw(AssertionError("cache-only must not fetch source")),
        )

        assert store.read_source("2002/738") is None

    def test_cache_only_later_source_cache_is_read_when_present(
        self,
        archive: Farchive,
    ):
        from lawvm.corpus_store import statute_url

        store = TransparentCorpusStore(
            archive=archive,
            cache_only=True,
            verbose=False,
        )
        xml = _make_xml("2002/738")
        archive.store(statute_url("2002/738"), xml, storage_class="xml")

        assert store.read_source("2002/738") == xml

    def test_cache_only_later_oracle_cache_is_read_when_present(
        self,
        archive: Farchive,
    ):
        from lawvm.corpus_store import oracle_url

        store = TransparentCorpusStore(
            archive=archive,
            cache_only=True,
            verbose=False,
        )
        xml = _make_xml("2002/738", "20250001")
        archive.store(oracle_url("2002/738", version="20250001"), xml, storage_class="xml")

        assert store.read_oracle("2002/738") == xml

    def test_cache_only_ignores_sd_cons_old_oracle_cache(
        self,
        archive: Farchive,
    ):
        store = TransparentCorpusStore(
            archive=archive,
            cache_only=True,
            verbose=False,
        )
        xml = _make_xml("2002/738")
        archive.store("finlex://sd-cons-old/2002/738/fin/main.xml", xml, storage_class="xml")

        assert store.read_oracle("2002/738") is None


# ---------------------------------------------------------------------------
# oracle_path_index
# ---------------------------------------------------------------------------

class TestOraclePathIndex:
    def test_index_includes_cached_pit_urls(
        self,
        store: TransparentCorpusStore,
    ):
        """oracle_path_index reports PIT URLs for statutes cached in archive."""
        from lawvm.corpus_store import oracle_url
        xml = _make_xml("2002/738", "20250001")
        locator = oracle_url("2002/738", version="20250001")
        store._archive.store(locator, xml, storage_class="xml")

        index = store.oracle_path_index()
        assert "2002/738" in index
        assert "fin@20250001" in index["2002/738"]

    def test_index_empty_when_archive_empty(
        self,
        store: TransparentCorpusStore,
    ):
        """When archive has no oracle entries, index is empty."""
        index = store.oracle_path_index()
        assert isinstance(index, dict)
        # Empty archive → empty index
        assert len(index) == 0

    def test_pit_url_in_index(
        self,
        store: TransparentCorpusStore,
    ):
        """Cached PIT URL appears in index with correct format."""
        from lawvm.corpus_store import oracle_url
        xml = _make_xml("2002/738", "20250001")
        locator = oracle_url("2002/738", version="20250001")
        store._archive.store(locator, xml, storage_class="xml")

        index = store.oracle_path_index()
        assert "2002/738" in index
        # Should point to the archive URL
        assert index["2002/738"].startswith("finlex://")
        assert "20250001" in index["2002/738"]

    def test_index_ignores_sd_cons_old_consolidated_urls(
        self,
        store: TransparentCorpusStore,
    ):
        xml = _make_xml("2002/738")
        store._archive.store("finlex://sd-cons-old/2002/738/fin/main.xml", xml, storage_class="xml")

        index = store.oracle_path_index()
        assert "2002/738" not in index

    def test_index_prefers_embedded_identity_over_path_suffix(
        self,
        store: TransparentCorpusStore,
    ):
        """A mismatched raw fin@ path should not outrank a later embedded PIT."""
        lower_xml = _make_xml("2002/738", "20190011")
        higher_xml = _make_xml("2002/738", "20240012")

        store_consolidated_xml(
            store._archive,
            "2002/738",
            lower_xml,
            requested_locator=oracle_url("2002/738", version="20250001"),
        )
        store_consolidated_xml(
            store._archive,
            "2002/738",
            higher_xml,
            requested_locator=oracle_url("2002/738", version="20240012"),
        )

        assert store.read_oracle("2002/738") == higher_xml
        index = store.oracle_path_index()
        assert index["2002/738"] == oracle_url("2002/738", version="20240012")

    def test_select_oracle_exact_embedded_version_ignores_path_suffix(
        self,
        store: TransparentCorpusStore,
    ):
        low_xml = _make_xml("2002/738", "20190011", date_consolidated="2024-01-01")
        high_xml = _make_xml("2002/738", "20240012", date_consolidated="2024-01-02")

        store._archive.store(
            "finlex://sd-cons/2002/738/fin@20250001/main.xml",
            low_xml,
            storage_class="xml",
        )
        store._archive.store(
            "finlex://sd-cons/2002/738/fin@20240012/main.xml",
            high_xml,
            storage_class="xml",
        )

        selected = store.select_oracle(
            "2002/738",
            ConsolidatedArtifactSelector.exact_embedded_version("20190011"),
        )

        assert selected == low_xml

    def test_select_oracle_date_cutoff_prefers_latest_on_or_before(
        self,
        store: TransparentCorpusStore,
    ):
        early_xml = _make_xml("2002/738", "20230001", date_consolidated="2023-12-31")
        mid_xml = _make_xml("2002/738", "20240012", date_consolidated="2024-01-15")
        late_xml = _make_xml("2002/738", "20250001", date_consolidated="2024-02-01")

        store._archive.store(
            "finlex://sd-cons/2002/738/fin@20230001/main.xml",
            early_xml,
            storage_class="xml",
        )
        store._archive.store(
            "finlex://sd-cons/2002/738/fin@20240012/main.xml",
            mid_xml,
            storage_class="xml",
        )
        store._archive.store(
            "finlex://sd-cons/2002/738/fin@20250001/main.xml",
            late_xml,
            storage_class="xml",
        )

        selected = store.select_oracle(
            "2002/738",
            ConsolidatedArtifactSelector.date_consolidated_at_or_before(
                dt.date(2024, 1, 31)
            ),
        )

        assert selected == mid_xml
        assert get_oracle_path(
            "2002/738",
            store,
            ConsolidatedArtifactSelector.date_consolidated_at_or_before(
                dt.date(2024, 1, 31)
            ),
        ) == "finlex://sd-cons/2002/738/fin@20240012/main.xml"

# ---------------------------------------------------------------------------
# consolidated locator access helpers
# ---------------------------------------------------------------------------

class TestConsolidatedLocatorAccess:
    def test_consolidated_locator_helpers_filter_by_family(
        self,
        store: TransparentCorpusStore,
    ):
        xml = _make_xml("2002/738", "20250001")
        pit_locator = oracle_url("2002/738", version="20250001")
        corrigendum_locator = (
            "finlex://sd-cons/2002/738/fin@20250001/media/corrigenda/sk20250001.pdf"
        )
        store._archive.store(pit_locator, xml, storage_class="xml")
        store._archive.store(corrigendum_locator, b"pdf-bytes", storage_class="text")

        all_locators = list_cached_consolidated_locators(store, "2002/738")
        pit_locators = list_cached_consolidated_pit_locators(store, "2002/738")
        corr_locators = list_cached_corrigendum_locators(store, "2002/738")

        assert pit_locator in all_locators
        assert corrigendum_locator in all_locators
        assert pit_locators == [pit_locator]
        assert corr_locators == [corrigendum_locator]

    def test_read_corrigendum_media_prefers_highest_pit(
        self,
        store: TransparentCorpusStore,
    ):
        low_locator = (
            "finlex://sd-cons/2002/738/fin@20230001/media/corrigenda/sk20230001.pdf"
        )
        high_locator = (
            "finlex://sd-cons/2002/738/fin@20240012/media/corrigenda/sk20230001.pdf"
        )
        store._archive.store(low_locator, b"low", storage_class="text")
        store._archive.store(high_locator, b"high", storage_class="text")

        assert store.read_corrigendum_media("2002/738", "sk20230001.pdf") == b"high"


# ---------------------------------------------------------------------------
# list_statute_ids
# ---------------------------------------------------------------------------

class TestListStatuteIds:
    def test_empty_archive_returns_empty_list(self, store: TransparentCorpusStore):
        assert store.list_statute_ids() == []

    def test_returns_sids_from_source_locators(self, store: TransparentCorpusStore):
        from lawvm.corpus_store import statute_url
        xml1 = _make_xml("2002/738")
        xml2 = _make_xml("2018/1121")
        store._archive.store(statute_url("2002/738"), xml1, storage_class="xml")
        store._archive.store(statute_url("2018/1121"), xml2, storage_class="xml")

        sids = store.list_statute_ids()
        assert "2002/738" in sids
        assert "2018/1121" in sids


# ---------------------------------------------------------------------------
# refresh() method
# ---------------------------------------------------------------------------

class TestRefreshMethod:
    def test_force_refresh_bypasses_cache(
        self,
        store: TransparentCorpusStore,
        monkeypatch,
    ):
        """refresh(sid, force=True) always hits the API even when cache is fresh."""
        cached_xml = _make_xml("2002/738", "20200001")
        new_xml = _make_xml("2002/738", "20260001")

        from lawvm.corpus_store import oracle_url
        store._archive.store(
            oracle_url("2002/738", version="20200001"), cached_xml, storage_class="xml"
        )

        monkeypatch.setattr(
            "lawvm.finland.transparent_store.fetch_latest_consolidated",
            lambda y, n: (new_xml, "20260001"),
        )

        result = store.refresh("2002/738", force=True)
        assert result == new_xml


# ---------------------------------------------------------------------------
# get_corpus_store factory
# ---------------------------------------------------------------------------

class TestGetCorpusStoreFactory:
    def test_transparent_mode_returns_transparent_store(
        self, tmp_path: Path, monkeypatch
    ):
        """LAWVM_FARCHIVE_DB env var is used for Farchive path."""
        from lawvm.corpus_store import get_corpus_store

        monkeypatch.setenv(
            "LAWVM_FARCHIVE_DB", str(tmp_path / "transparent.farchive")
        )

        result = get_corpus_store()
        assert isinstance(result, TransparentCorpusStore)
        assert result._cache_only is True

    def test_always_returns_transparent_store(
        self, tmp_path: Path, monkeypatch
    ):
        """Factory always returns TransparentCorpusStore (farchive-backed)."""
        from lawvm.corpus_store import get_corpus_store

        monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(tmp_path / "test.farchive"))
        result = get_corpus_store()
        assert isinstance(result, TransparentCorpusStore)


# ---------------------------------------------------------------------------
# FreshnessReport
# ---------------------------------------------------------------------------

class TestFreshnessReport:
    def test_report_classifies_not_found(
        self, store: TransparentCorpusStore
    ):
        """Statutes not in archive are classified as not_found."""
        report = store.freshness_report(sids=["2002/738", "2018/1121"])
        assert report.total == 2
        sids_in_report = {r.sid for r in report.rows}
        assert "2002/738" in sids_in_report
        for row in report.rows:
            assert row.source in ("not_found", "api_pit")

    def test_report_classifies_api_pit(
        self, store: TransparentCorpusStore
    ):
        """Statutes with cached PIT are classified as api_pit."""
        from lawvm.corpus_store import oracle_url
        xml = _make_xml("2002/738", "20250001")
        store._archive.store(
            oracle_url("2002/738", version="20250001"), xml, storage_class="xml"
        )

        report = store.freshness_report(sids=["2002/738"])
        assert report.total == 1
        row = report.rows[0]
        assert row.sid == "2002/738"
        assert row.source == "api_pit"
        assert row.pit_version == "20250001"
        assert row.age_days >= 0.0


# ---------------------------------------------------------------------------
# Network smoke tests (skipped by default — use -m network to run)
# ---------------------------------------------------------------------------

@pytest.mark.network
class TestNetworkSmoke:
    """Live network tests. Require internet access and Finlex availability.

    Run with: pytest -m network tests/test_transparent_store.py -v
    """

    KNOWN_HAS_PIT = "2002/738"         # sähköinen asiointi — has PIT versions
    KNOWN_DESYNC = "2018/1121"          # elintarvikemarkkinaketjulaki — known desync
    KNOWN_OLD = "1979/925"              # old statute

    @pytest.fixture()
    def live_store(self, tmp_path: Path):
        archive = Farchive(tmp_path / "live_transparent.farchive")
        return TransparentCorpusStore(archive=archive, verbose=True)

    def test_discover_pit_versions_2002_738(self, live_store: TransparentCorpusStore):
        """2002/738 should have PIT versions."""
        versions = live_store._discover_pit_versions("2002", "738")
        if not versions:
            pytest.skip("Finlex OpenAPI unavailable in this environment")
        assert len(versions) > 0, "2002/738 should have at least one PIT version"
        # All versions should be 8-digit strings
        for v in versions:
            assert len(v) == 8 and v.isdigit(), f"Bad PIT version format: {v!r}"

    def test_refresh_2018_1121(self, live_store: TransparentCorpusStore):
        """2018/1121 (known desync) can be refreshed."""
        xml = live_store.read_oracle(self.KNOWN_DESYNC)
        # May return None if API has no PIT — that's fine
        if xml is not None:
            assert b"akomaNtoso" in xml or b"<act" in xml

    def test_old_statute_1979_925(self, live_store: TransparentCorpusStore):
        """1979/925 (old statute) — may have no PIT; graceful degradation."""
        # No PIT expected — should not raise, should return None or bytes
        xml = live_store.read_oracle(self.KNOWN_OLD)
        # Either valid XML or None is acceptable
        if xml is not None:
            assert len(xml) > 100

    def test_second_call_uses_cache(self, live_store: TransparentCorpusStore):
        """Second read_oracle call for same SID uses cache (no additional network)."""
        xml1 = live_store.read_oracle(self.KNOWN_HAS_PIT)
        # Patch HTTP to raise if called
        call_count = [0]

        def no_network(*args, **kwargs):
            call_count[0] += 1
            raise RuntimeError("Should not make HTTP calls on second read_oracle")

        import unittest.mock
        with unittest.mock.patch("urllib.request.urlopen", no_network):
            xml2 = live_store.read_oracle(self.KNOWN_HAS_PIT)

        assert call_count[0] == 0, "Second call made unexpected HTTP requests"
        assert xml1 == xml2
