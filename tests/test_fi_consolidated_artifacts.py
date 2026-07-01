from __future__ import annotations

import datetime as dt
from typing import cast

from lxml import etree

from lawvm.corpus_store import CorpusStore
from lawvm.finland import consolidated_store
from lawvm.finland import corpus as fi_corpus
from lawvm.finland.consolidated_artifacts import (
    ConsolidatedArtifactSelector,
    canonical_consolidated_locator,
    consolidated_family_key,
    consolidated_locator_sort_key,
    extract_consolidated_xml_identity,
)
from lawvm.finland.transparent_store import TransparentCorpusStore


def _xml(*, frbrthis_version: str, frbrversion_number: str, date_consolidated: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta>
      <identification>
        <FRBRWork>
          <FRBRthis value="/akn/fi/act/statute-consolidated/2014/1429/fin@{frbrthis_version}/!main"/>
        </FRBRWork>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="{frbrversion_number}"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRdate name="dateConsolidated" date="{date_consolidated}"/>
        </FRBRManifestation>
      </identification>
    </meta>
  </act>
</akomaNtoso>
""".encode("utf-8")


def _xml_lang(
    *,
    lang: str,
    frbrthis_version: str,
    frbrversion_number: str,
    date_consolidated: str,
) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta>
      <identification>
        <FRBRWork>
          <FRBRthis value="/akn/fi/act/statute-consolidated/2014/1429/{lang}@{frbrthis_version}/!main"/>
        </FRBRWork>
        <FRBRExpression>
          <FRBRlanguage language="{lang}"/>
          <FRBRversionNumber value="{frbrversion_number}"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRdate name="dateConsolidated" date="{date_consolidated}"/>
        </FRBRManifestation>
      </identification>
    </meta>
  </act>
</akomaNtoso>
""".encode("utf-8")


def _source_xml(*, effective_date: str, issued_date: str | None = None) -> bytes:
    issued = issued_date or effective_date
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta>
      <identification>
        <FRBRManifestation>
          <FRBRdate name="dateIssued" date="{issued}"/>
        </FRBRManifestation>
      </identification>
      <proprietary>
        <dateEntryIntoForce date="{effective_date}"/>
      </proprietary>
    </meta>
    <dateEntryIntoForce date="{effective_date}"/>
  </act>
</akomaNtoso>
""".encode("utf-8")


def test_strip_editorial_notes_removes_prior_wording_sibling() -> None:
    root = etree.fromstring(
        b"""<body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
             xmlns:finlex="http://data.finlex.fi/schema/finlex">
          <section eId="sec_4av20100489">
            <num>4 a \xc2\xa7</num>
            <subsection eId="sec_4av20100489__subsec_1v20230053">
              <content><p>Current first subsection.</p></content>
            </subsection>
            <hcontainer eId="note_3" name="noteAuthorial" finlex:outline="huomautus">
              <content><p>L:lla 53/2023 muutettu 1 momentti tulee voimaan 1.6.2023. Aiempi sanamuoto kuuluu:</p></content>
            </hcontainer>
            <subsection eId="sec_4av20100489__subsec_1v20100489">
              <content><p>Prior first subsection.</p></content>
            </subsection>
            <subsection eId="sec_4av20100489__subsec_2v20230806">
              <content><p>Current second subsection.</p></content>
            </subsection>
          </section>
        </body>"""
    )

    fi_corpus._strip_editorial_note_containers(root)

    text = etree.tostring(root, method="text", encoding="unicode")
    assert "Current first subsection." in text
    assert "Current second subsection." in text
    assert "Aiempi sanamuoto" not in text
    assert "Prior first subsection." not in text


def test_extract_consolidated_xml_identity_prefers_frbrthis_version() -> None:
    identity = extract_consolidated_xml_identity(
        _xml(
            frbrthis_version="20190112",
            frbrversion_number="20251497",
            date_consolidated="2024-12-19",
        )
    )

    assert identity.embedded_version_tag == "20190112"
    assert str(identity.date_consolidated) == "2024-12-19"


def test_extract_consolidated_xml_identity_supports_preferred_swe_language() -> None:
    identity = extract_consolidated_xml_identity(
        _xml_lang(
            lang="swe",
            frbrthis_version="20190112",
            frbrversion_number="20190112",
            date_consolidated="2024-12-19",
        ),
        preferred_lang="swe",
    )

    assert identity.embedded_version_tag == "20190112"
    assert identity.embedded_frbrthis.endswith("/swe@20190112/!main")


def test_canonical_consolidated_locator_uses_embedded_identity() -> None:
    locator = "finlex://sd-cons-old/2014/1429/fin@20251497/main.xml"

    canonical = canonical_consolidated_locator(locator, version_tag="20190112")

    assert canonical == "finlex://sd-cons/2014/1429/fin@20190112/main.xml"


def test_consolidated_family_key_tracks_source_family_before_normalization() -> None:
    locator = "finlex://sd-cons/2014/1429/fin@20251497/media/corrigenda/x.gif"

    assert consolidated_family_key(locator) == ("2014/1429", "fin", "20251497")


def test_consolidated_locator_sort_key_prefers_embedded_identity_over_path_suffix() -> None:
    low_embedded_high_path = _xml(
        frbrthis_version="20190011",
        frbrversion_number="20190011",
        date_consolidated="2024-01-01",
    )
    high_embedded_lower_path = _xml(
        frbrthis_version="20240012",
        frbrversion_number="20240012",
        date_consolidated="2024-01-02",
    )

    lower_key = consolidated_locator_sort_key(
        "finlex://sd-cons/2014/1429/fin@20250001/main.xml",
        low_embedded_high_path,
    )
    higher_key = consolidated_locator_sort_key(
        "finlex://sd-cons/2014/1429/fin@20240012/main.xml",
        high_embedded_lower_path,
    )

    assert lower_key < higher_key


def test_best_cached_consolidated_path_index_returns_canonical_locator_from_identity() -> None:
    class DummyArchive:
        def locators(self, pattern: str = "%") -> list[str]:
            assert pattern == "finlex://sd-cons/%/fin@%/main.xml"
            return ["finlex://sd-cons/2014/1429/fin@20250001/main.xml"]

        def get(self, url: str) -> bytes | None:
            assert url == "finlex://sd-cons/2014/1429/fin@20250001/main.xml"
            return _xml(
                frbrthis_version="20190112",
                frbrversion_number="20250001",
                date_consolidated="2024-01-02",
            )

    index = consolidated_store.best_cached_consolidated_path_index(DummyArchive())

    assert index == {
        "2014/1429": "finlex://sd-cons/2014/1429/fin@20190112/main.xml",
    }


def test_select_cached_consolidated_artifact_exact_embedded_version_ignores_path_suffix() -> None:
    class DummyArchive:
        def locators(self, pattern: str = "%") -> list[str]:
            assert pattern == "finlex://sd-cons/2014/1429/fin@%/main.xml"
            return [
                "finlex://sd-cons/2014/1429/fin@20250001/main.xml",
                "finlex://sd-cons/2014/1429/fin@20240012/main.xml",
            ]

        def get(self, url: str) -> bytes | None:
            payloads = {
                "finlex://sd-cons/2014/1429/fin@20250001/main.xml": _xml(
                    frbrthis_version="20190011",
                    frbrversion_number="20250001",
                    date_consolidated="2024-01-01",
                ),
                "finlex://sd-cons/2014/1429/fin@20240012/main.xml": _xml(
                    frbrthis_version="20240012",
                    frbrversion_number="20240012",
                    date_consolidated="2024-01-02",
                ),
            }
            return payloads[url]

    artifact = consolidated_store.select_cached_consolidated_artifact(
        DummyArchive(),
        "2014/1429",
        selector=ConsolidatedArtifactSelector.exact_embedded_version("20190011"),
    )

    assert artifact is not None
    assert artifact.version_tag == "20190011"
    assert artifact.canonical_locator == "finlex://sd-cons/2014/1429/fin@20190011/main.xml"


def test_select_cached_consolidated_artifact_latest_cached_editorial_uses_embedded_identity() -> None:
    class DummyArchive:
        def locators(self, pattern: str = "%") -> list[str]:
            assert pattern == "finlex://sd-cons/2014/1429/fin@%/main.xml"
            return [
                "finlex://sd-cons/2014/1429/fin@20250001/main.xml",
                "finlex://sd-cons/2014/1429/fin@20240012/main.xml",
            ]

        def get(self, url: str) -> bytes | None:
            payloads = {
                "finlex://sd-cons/2014/1429/fin@20250001/main.xml": _xml(
                    frbrthis_version="20190011",
                    frbrversion_number="20250001",
                    date_consolidated="2024-01-01",
                ),
                "finlex://sd-cons/2014/1429/fin@20240012/main.xml": _xml(
                    frbrthis_version="20240012",
                    frbrversion_number="20240012",
                    date_consolidated="2024-01-02",
                ),
            }
            return payloads[url]

    artifact = consolidated_store.select_cached_consolidated_artifact(
        DummyArchive(),
        "2014/1429",
        selector=ConsolidatedArtifactSelector.latest_cached_editorial(),
    )

    assert artifact is not None
    assert artifact.version_tag == "20240012"
    assert artifact.canonical_locator == "finlex://sd-cons/2014/1429/fin@20240012/main.xml"


def test_select_cached_consolidated_artifact_reuses_metadata_without_retaining_xml(monkeypatch) -> None:
    locators = [
        "finlex://sd-cons/2014/1429/fin@20250001/main.xml",
        "finlex://sd-cons/2014/1429/fin@20240012/main.xml",
    ]
    payloads = {
        locators[0]: _xml(
            frbrthis_version="20190011",
            frbrversion_number="20250001",
            date_consolidated="2024-01-01",
        ),
        locators[1]: _xml(
            frbrthis_version="20240012",
            frbrversion_number="20240012",
            date_consolidated="2024-01-02",
        ),
    }

    class DummyArchive:
        def locators(self, pattern: str = "%") -> list[str]:
            assert pattern == "finlex://sd-cons/2014/1429/fin@%/main.xml"
            return locators

        def get(self, url: str) -> bytes | None:
            return payloads[url]

    consolidated_store._clear_artifact_record_cache_for_tests()
    parse_count = 0
    original_artifact_record = consolidated_store.artifact_record

    def counted_artifact_record(locator: str, xml: bytes):
        nonlocal parse_count
        parse_count += 1
        return original_artifact_record(locator, xml)

    monkeypatch.setattr(consolidated_store, "artifact_record", counted_artifact_record)

    first = consolidated_store.select_cached_consolidated_artifact(DummyArchive(), "2014/1429")
    second = consolidated_store.select_cached_consolidated_artifact(DummyArchive(), "2014/1429")

    assert first is not None
    assert second is not None
    assert first.version_tag == second.version_tag == "20240012"
    assert parse_count == 2

    payloads[locators[1]] = _xml(
        frbrthis_version="20240013",
        frbrversion_number="20240013",
        date_consolidated="2024-01-03",
    )

    third = consolidated_store.select_cached_consolidated_artifact(DummyArchive(), "2014/1429")

    assert third is not None
    assert third.version_tag == "20240013"
    assert parse_count == 3
    consolidated_store._clear_artifact_record_cache_for_tests()


def test_transparent_store_read_oracle_reuses_selected_pit_within_store() -> None:
    locators = [
        "finlex://sd-cons/2014/1429/fin@20240012/main.xml",
    ]
    payloads = {
        locators[0]: _xml(
            frbrthis_version="20240012",
            frbrversion_number="20240012",
            date_consolidated="2024-01-02",
        ),
    }

    class DummyArchive:
        def __init__(self) -> None:
            self.locator_calls = 0

        def locators(self, pattern: str = "%") -> list[str]:
            assert pattern == "finlex://sd-cons/2014/1429/fin@%/main.xml"
            self.locator_calls += 1
            return locators

        def get(self, url: str) -> bytes | None:
            return payloads[url]

        def has(self, url: str, **_kwargs: object) -> bool:
            return url in payloads

    archive = DummyArchive()
    store = TransparentCorpusStore(archive, cache_only=True)

    first = store.read_oracle("2014/1429")
    second = store.read_oracle("2014/1429")

    assert first == second == payloads[locators[0]]
    assert archive.locator_calls == 1


def test_selected_consolidated_locator_index_cache_is_scoped_to_corpus_and_clearable() -> None:
    locators = [
        "finlex://sd-cons/2014/1429/fin@20240012/main.xml",
    ]

    class DummyArchive:
        locator_calls = 0

        def locators(self, pattern: str = "%") -> list[str]:
            self.locator_calls += 1
            raise AssertionError("default locator-only selection must use oracle_path_index")

        def get(self, url: str) -> bytes | None:
            raise AssertionError("default locator-only selection must not read XML")

    class DummyCorpus:
        def __init__(self) -> None:
            self._archive = DummyArchive()
            self.index_calls = 0

        def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
            assert kwargs == {}
            self.index_calls += 1
            return {"2014/1429": locators[0]}

    fi_corpus._clear_selected_consolidated_locator_cache_for_tests()
    corpus = DummyCorpus()
    corpus_typed = cast(CorpusStore, corpus)

    first = fi_corpus.get_oracle_path("2014/1429", corpus=corpus_typed)
    second = fi_corpus.get_oracle_path("2014/1429", corpus=corpus_typed)

    assert first == second == "finlex://sd-cons/2014/1429/fin@20240012/main.xml"
    assert corpus.index_calls == 1
    assert corpus._archive.locator_calls == 0

    fi_corpus._clear_selected_consolidated_locator_cache_for_tests()
    third = fi_corpus.get_oracle_path("2014/1429", corpus=corpus_typed)

    assert third == first
    assert corpus.index_calls == 2
    assert corpus._archive.locator_calls == 0


def test_selected_consolidated_provenance_still_uses_archive_selection() -> None:
    locators = [
        "finlex://sd-cons/2014/1429/fin@20240012/main.xml",
    ]
    payloads = {
        locators[0]: _xml(
            frbrthis_version="20240012",
            frbrversion_number="20240012",
            date_consolidated="2024-01-02",
        ),
    }

    class DummyArchive:
        locator_calls = 0

        def locators(self, pattern: str = "%") -> list[str]:
            assert pattern == "finlex://sd-cons/2014/1429/fin@%/main.xml"
            self.locator_calls += 1
            return locators

        def get(self, url: str) -> bytes | None:
            return payloads[url]

    class DummyCorpus:
        def __init__(self) -> None:
            self._archive = DummyArchive()

        def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
            raise AssertionError("provenance-bearing selection must inspect artifacts")

    fi_corpus._clear_selected_consolidated_locator_cache_for_tests()
    corpus = DummyCorpus()
    corpus_typed = cast(CorpusStore, corpus)

    first = fi_corpus.get_oracle_selection_provenance("2014/1429", corpus=corpus_typed)
    second = fi_corpus.get_oracle_selection_provenance("2014/1429", corpus=corpus_typed)

    assert first == second
    assert first is not None
    assert first.chosen_version_tag == "20240012"
    assert corpus._archive.locator_calls == 1


def test_consolidated_oracle_context_cache_reuses_selected_locator_xml() -> None:
    locators = [
        "finlex://sd-cons/2014/1429/fin@20240012/main.xml",
    ]
    payloads = {
        locators[0]: _xml(
            frbrthis_version="20240012",
            frbrversion_number="20240012",
            date_consolidated="2024-01-02",
        ),
    }

    class DummyArchive:
        def locators(self, pattern: str = "%") -> list[str]:
            raise AssertionError("default context selection must use oracle_path_index")

        def get(self, url: str) -> bytes | None:
            raise AssertionError("default context selection reads through corpus.read_locator")

    class DummyCorpus:
        def __init__(self) -> None:
            self._archive = DummyArchive()
            self.read_locator_calls = 0

        def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
            assert kwargs == {}
            return {"2014/1429": locators[0]}

        def read_locator(self, locator: str) -> bytes | None:
            self.read_locator_calls += 1
            return payloads[locator]

    fi_corpus._clear_selected_consolidated_locator_cache_for_tests()
    corpus = DummyCorpus()
    corpus_typed = cast(CorpusStore, corpus)

    first = fi_corpus.get_consolidated_meta("2014/1429", corpus=corpus_typed)
    second = fi_corpus.get_consolidated_meta("2014/1429", corpus=corpus_typed)

    assert first == second == (dt.date(2024, 1, 2), "2024/12")
    assert corpus.read_locator_calls == 1

    fi_corpus._clear_selected_consolidated_locator_cache_for_tests()
    third = fi_corpus.get_consolidated_meta("2014/1429", corpus=corpus_typed)

    assert third == first
    assert corpus.read_locator_calls == 2


def test_select_cached_consolidated_artifact_date_cutoff_selects_latest_on_or_before() -> None:
    class DummyArchive:
        def locators(self, pattern: str = "%") -> list[str]:
            assert pattern == "finlex://sd-cons/2014/1429/fin@%/main.xml"
            return [
                "finlex://sd-cons/2014/1429/fin@20250001/main.xml",
                "finlex://sd-cons/2014/1429/fin@20240012/main.xml",
                "finlex://sd-cons/2014/1429/fin@20230001/main.xml",
            ]

        def get(self, url: str) -> bytes | None:
            payloads = {
                "finlex://sd-cons/2014/1429/fin@20250001/main.xml": _xml(
                    frbrthis_version="20250001",
                    frbrversion_number="20250001",
                    date_consolidated="2024-02-01",
                ),
                "finlex://sd-cons/2014/1429/fin@20240012/main.xml": _xml(
                    frbrthis_version="20240012",
                    frbrversion_number="20240012",
                    date_consolidated="2024-01-15",
                ),
                "finlex://sd-cons/2014/1429/fin@20230001/main.xml": _xml(
                    frbrthis_version="20230001",
                    frbrversion_number="20230001",
                    date_consolidated="2023-12-31",
                ),
            }
            return payloads[url]

    artifact = consolidated_store.select_cached_consolidated_artifact(
        DummyArchive(),
        "2014/1429",
        selector=ConsolidatedArtifactSelector.date_consolidated_at_or_before(
            dt.date(2024, 1, 31)
        ),
    )

    assert artifact is not None
    assert artifact.version_tag == "20240012"
    assert artifact.date_consolidated == dt.date(2024, 1, 15)


def test_select_cached_consolidated_artifact_bench_comparable_prefers_self_commensurable() -> None:
    """Option Z with 180-day tolerance: bench_comparable rejects artifacts
    whose embedded amendment's effective date is >180 days past the
    date_consolidated stamp, and falls back to an older self-comparable
    variant.

    Fixture:
    - ``20990001`` has effective 2099-02-01 and date_consolidated 2098-01-15.
      Gap = ~383 days → **rejected** by the 180-day tolerance refinement
      added in T5-fix (a3870eea).
    - ``20240012`` has effective 2024-01-01 and date_consolidated 2024-01-15.
      Already-in-force (negative gap) → accepted.

    The older variant is selected because the newer one is outside the
    Finlex drafting lead-time window and is treated as a real metadata
    inconsistency rather than a collapsed-dates pathology.

    Provenance: T5 (commit dd3d631c) introduced Option Z. T5-fix
    (commit a3870eea) restored the 180-day tolerance matching the
    long-standing ``corpus.py:404`` convention.
    """
    class DummyArchive:
        def locators(self, pattern: str = "%") -> list[str]:
            assert pattern == "finlex://sd-cons/2014/1429/fin@%/main.xml"
            return [
                "finlex://sd-cons/2014/1429/fin@20990001/main.xml",
                "finlex://sd-cons/2014/1429/fin@20240012/main.xml",
            ]

        def get(self, url: str) -> bytes | None:
            payloads = {
                "finlex://sd-cons/2014/1429/fin@20990001/main.xml": _xml(
                    frbrthis_version="20990001",
                    frbrversion_number="20990001",
                    date_consolidated="2098-01-15",
                ),
                "finlex://sd-cons/2014/1429/fin@20240012/main.xml": _xml(
                    frbrthis_version="20240012",
                    frbrversion_number="20240012",
                    date_consolidated="2024-01-15",
                ),
                "finlex://sd/2099/1/fin/main.xml": _source_xml(effective_date="2099-02-01"),
                "finlex://sd/2024/12/fin/main.xml": _source_xml(effective_date="2024-01-01"),
            }
            return payloads.get(url)

    artifact = consolidated_store.select_cached_consolidated_artifact(
        DummyArchive(),
        "2014/1429",
        selector=ConsolidatedArtifactSelector.bench_comparable(),
    )

    # 180-day tolerance: 20990001 rejected (gap ~383 days); 20240012 wins.
    assert artifact is not None
    assert artifact.version_tag == "20240012"
    assert artifact.canonical_locator == "finlex://sd-cons/2014/1429/fin@20240012/main.xml"


def test_select_cached_consolidated_artifact_bench_comparable_keeps_commenced_early_artifact() -> None:
    class DummyArchive:
        def locators(self, pattern: str = "%") -> list[str]:
            assert pattern == "finlex://sd-cons/2014/1429/fin@%/main.xml"
            return [
                "finlex://sd-cons/2014/1429/fin@20250001/main.xml",
                "finlex://sd-cons/2014/1429/fin@20240012/main.xml",
            ]

        def get(self, url: str) -> bytes | None:
            payloads = {
                "finlex://sd-cons/2014/1429/fin@20250001/main.xml": _xml(
                    frbrthis_version="20250001",
                    frbrversion_number="20250001",
                    date_consolidated="2024-01-15",
                ),
                "finlex://sd-cons/2014/1429/fin@20240012/main.xml": _xml(
                    frbrthis_version="20240012",
                    frbrversion_number="20240012",
                    date_consolidated="2024-01-15",
                ),
                "finlex://sd/2025/1/fin/main.xml": _source_xml(effective_date="2025-02-01"),
                "finlex://sd/2024/12/fin/main.xml": _source_xml(effective_date="2024-01-01"),
            }
            return payloads.get(url)

    artifact = consolidated_store.select_cached_consolidated_artifact(
        DummyArchive(),
        "2014/1429",
        selector=ConsolidatedArtifactSelector.bench_comparable(),
    )

    assert artifact is not None
    assert artifact.version_tag == "20250001"
