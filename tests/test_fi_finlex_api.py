from __future__ import annotations

from lawvm.finland import finlex_api


def test_store_consolidated_xml_uses_embedded_identity_only():
    class DummyArchive:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[tuple[str, bytes]]]] = []

        def store_batch(self, batch: list[tuple[str, bytes]], storage_class: str) -> object:
            self.calls.append((storage_class, list(batch)))
            return object()

    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta>
      <identification>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="20190011"/>
        </FRBRExpression>
        <FRBRWork>
          <FRBRthis value="/akn/fi/act/statute-consolidated/2016/1227/fin@20190011/!main"/>
        </FRBRWork>
      </identification>
    </meta>
    <body/>
  </act>
</akomaNtoso>
"""

    archive = DummyArchive()
    result = finlex_api.store_consolidated_xml(
        archive,
        "2016/1227",
        xml,
        requested_locator="finlex://sd-cons/2016/1227/fin@20230883/main.xml",
    )

    assert result.embedded_version == "20190011"
    assert result.canonical_locator == "finlex://sd-cons/2016/1227/fin@20190011/main.xml"
    assert result.stored_locators == (
        "finlex://sd-cons/2016/1227/fin@20190011/main.xml",
    )
    assert archive.calls == [
        (
            "xml",
            [
                ("finlex://sd-cons/2016/1227/fin@20190011/main.xml", xml),
            ],
        )
    ]


def test_store_consolidated_xml_defaults_to_versioned_sd_cons_namespace():
    class DummyArchive:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[tuple[str, bytes]]]] = []

        def store_batch(self, batch: list[tuple[str, bytes]], storage_class: str) -> object:
            self.calls.append((storage_class, list(batch)))
            return object()

    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta>
      <identification>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="20190011"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRdate name="dateConsolidated" date="2024-12-19"/>
        </FRBRManifestation>
      </identification>
    </meta>
    <body/>
  </act>
</akomaNtoso>
"""

    archive = DummyArchive()
    result = finlex_api.store_consolidated_xml(archive, "2016/1227", xml)

    assert result.canonical_locator == "finlex://sd-cons/2016/1227/fin@20190011/main.xml"
    assert result.stored_locators == (
        "finlex://sd-cons/2016/1227/fin@20190011/main.xml",
    )
    assert archive.calls == [
        (
            "xml",
            [
                ("finlex://sd-cons/2016/1227/fin@20190011/main.xml", xml),
            ],
        )
    ]


def test_list_consolidated_pit_versions_filters_fin_and_paginates(monkeypatch):
    page1 = b"""<AknXmlList><Results><akomaNtoso>
      <act><meta><identification>
        <FRBRExpression>
          <FRBRlanguage language="swe"/>
          <FRBRversionNumber value="20190943"/>
        </FRBRExpression>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="20210701"/>
        </FRBRExpression>
      </identification></meta></act>
    </akomaNtoso></Results></AknXmlList>"""
    page2 = b"""<AknXmlList><Results><akomaNtoso>
      <act><meta><identification>
        <FRBRExpression>
          <FRBRlanguage language="swe"/>
          <FRBRversionNumber value="20200400"/>
        </FRBRExpression>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="20230883"/>
        </FRBRExpression>
      </identification></meta></act>
    </akomaNtoso></Results></AknXmlList>"""

    urls: list[str] = []

    def fake_http_get(url: str, accept: str = "application/xml") -> bytes:
        urls.append(url)
        if "page=1" in url:
            return page1
        if "page=2" in url:
            return page2
        return b""

    monkeypatch.setattr(finlex_api, "_http_get", fake_http_get)

    versions = finlex_api.list_consolidated_pit_versions("2016", "1227", max_pages=5)

    assert versions == ["20210701", "20230883"]
    assert any("page=2" in url for url in urls)


def test_fetch_latest_pit_xml_uses_highest_version(monkeypatch):
    page1 = b"""<AknXmlList><Results><akomaNtoso>
      <act><meta><identification>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="20210701"/>
        </FRBRExpression>
      </identification></meta></act>
    </akomaNtoso></Results></AknXmlList>"""
    page2 = b"""<AknXmlList><Results><akomaNtoso>
      <act><meta><identification>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="20230883"/>
        </FRBRExpression>
      </identification></meta></act>
    </akomaNtoso></Results></AknXmlList>"""

    def fake_http_get(url: str, accept: str = "application/xml") -> bytes:
        if "page=1" in url:
            return page1
        if "page=2" in url:
            return page2
        return b""

    monkeypatch.setattr(finlex_api, "_http_get", fake_http_get)

    seen: dict[str, str] = {}

    def fake_fetch_statute_xml(
        year: str,
        num: str,
        doc_type: str = "statute-consolidated",
        lang_version: str = "fin@",
    ) -> bytes | None:
        seen["year"] = year
        seen["num"] = num
        seen["doc_type"] = doc_type
        seen["lang_version"] = lang_version
        return b"<xml/>"

    monkeypatch.setattr(finlex_api, "fetch_statute_xml", fake_fetch_statute_xml)

    xml, pit_version = finlex_api.fetch_latest_pit_xml("2016", "1227")

    assert xml == b"<xml/>"
    assert pit_version == "20230883"
    assert seen["lang_version"] == "fin@20230883"


def test_fetch_latest_consolidated_prefers_latest_date_over_highest_version(monkeypatch):
    page1 = b"""<AknXmlList><Results><akomaNtoso>
      <act><meta><identification>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="20230883"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRdate name="dateConsolidated" date="2024-01-01"/>
        </FRBRManifestation>
      </identification></meta></act>
    </akomaNtoso></Results></AknXmlList>"""
    page2 = b"""<AknXmlList><Results><akomaNtoso>
      <act><meta><identification>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="20210701"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRdate name="dateConsolidated" date="2024-02-01"/>
        </FRBRManifestation>
      </identification></meta></act>
    </akomaNtoso></Results></AknXmlList>"""

    urls: list[str] = []

    def fake_http_get(url: str, accept: str = "application/xml") -> bytes:
        urls.append(url)
        if "page=1" in url:
            return page1
        if "page=2" in url:
            return page2
        return b""

    seen: dict[str, str] = {}

    def fake_fetch_statute_xml(
        year: str,
        num: str,
        doc_type: str = "statute-consolidated",
        lang_version: str = "fin@",
    ) -> bytes | None:
        seen["lang_version"] = lang_version
        return f"<xml version={lang_version!r}/>".encode("utf-8")

    monkeypatch.setattr(finlex_api, "_http_get", fake_http_get)
    monkeypatch.setattr(finlex_api, "fetch_statute_xml", fake_fetch_statute_xml)

    xml, pit_version = finlex_api.fetch_latest_consolidated("2016", "1227")

    assert pit_version == "20210701"
    assert xml == b"<xml version='fin@20210701'/>"
    assert seen["lang_version"] == "fin@20210701"
    assert any("page=2" in url for url in urls)


def test_sync_latest_pits_fetches_all_versions_and_skips_cached(monkeypatch):
    monkeypatch.setattr(
        finlex_api,
        "list_consolidated_pit_versions",
        lambda year, num, max_pages=200: ["20210701", "20230883"],
    )

    seen: list[str] = []

    def fake_fetch_statute_xml(
        year: str,
        num: str,
        doc_type: str = "statute-consolidated",
        lang_version: str = "fin@",
    ) -> bytes | None:
        seen.append(lang_version)
        pit = lang_version.removeprefix("fin@")
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta>
      <identification>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="{pit}"/>
        </FRBRExpression>
      </identification>
    </meta>
    <body/>
  </act>
</akomaNtoso>
""".encode("utf-8")

    monkeypatch.setattr(finlex_api, "fetch_statute_xml", fake_fetch_statute_xml)

    class DummyArchive:
        def __init__(self) -> None:
            self.locators_seen: list[str] = []
            self.stored: list[tuple[str, bytes, str]] = []

        def has(self, locator: str) -> bool:
            self.locators_seen.append(locator)
            return locator.endswith("fin@20210701/main.xml")

        def store(self, locator: str, blob: bytes, storage_class: str = "xml") -> None:
            self.stored.append((locator, blob, storage_class))

    archive = DummyArchive()

    stats = finlex_api.sync_latest_pits(archive, ["2016/1227"], delay=0.0, verbose=False)

    assert seen == ["fin@20230883"]
    assert stats == {
        "cached": 1,
        "fetched": 1,
        "skipped": 0,
        "errors": 0,
        "statutes": 1,
    }
    assert archive.stored == [
        (
            "finlex://sd-cons/2016/1227/fin@20230883/main.xml",
            fake_fetch_statute_xml("2016", "1227", lang_version="fin@20230883"),
            "xml",
        ),
    ]


def test_sync_latest_pits_records_discovery_failure_diagnostic(monkeypatch):
    def fake_list_consolidated_pit_versions(_year: str, _num: str) -> list[str]:
        raise RuntimeError("listing failed")

    class DummyArchive:
        def has(self, _locator: str) -> bool:
            raise AssertionError("discovery failure should not inspect PIT locators")

    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(finlex_api, "list_consolidated_pit_versions", fake_list_consolidated_pit_versions)

    stats = finlex_api.sync_latest_pits(
        DummyArchive(),
        ["2016/1227"],
        delay=0.0,
        diagnostics_out=diagnostics,
    )

    assert stats == {
        "cached": 0,
        "fetched": 0,
        "skipped": 0,
        "errors": 1,
        "statutes": 1,
    }
    assert diagnostics == [
        {
            "rule_id": "fi_sync_latest_pit_discovery_failed",
            "phase": "acquisition",
            "family": "source_pathology",
            "statute_id": "2016/1227",
            "pit_version": "",
            "locator": "",
            "reason": "RuntimeError",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]
