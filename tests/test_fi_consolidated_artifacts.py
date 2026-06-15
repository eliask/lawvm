from __future__ import annotations

import datetime as dt

from lawvm.finland import consolidated_store
from lawvm.finland.consolidated_artifacts import (
    ConsolidatedArtifactSelector,
    canonical_consolidated_locator,
    consolidated_family_key,
    consolidated_locator_sort_key,
    extract_consolidated_xml_identity,
)


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
    - ``20250001`` has effective 2025-02-01 and date_consolidated 2024-01-15.
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

    # 180-day tolerance: 20250001 rejected (gap ~383 days); 20240012 wins.
    assert artifact is not None
    assert artifact.version_tag == "20240012"
    assert artifact.canonical_locator == "finlex://sd-cons/2014/1429/fin@20240012/main.xml"
