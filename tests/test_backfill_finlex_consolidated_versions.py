from __future__ import annotations

import importlib
from pathlib import Path

backfill = importlib.import_module("scripts.backfill_finlex_consolidated_versions")


def _consolidated_xml(pit_version: str, *, lang: str = "fin") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta>
      <identification>
        <FRBRWork>
          <FRBRthis value="/akn/fi/act/statute-consolidated/1988/46/{lang}@{pit_version}/!main"/>
        </FRBRWork>
        <FRBRExpression>
          <FRBRlanguage language="{lang}"/>
          <FRBRversionNumber value="{pit_version}"/>
        </FRBRExpression>
      </identification>
    </meta>
    <body><section eId="sec_1"><num>1</num></section></body>
  </act>
</akomaNtoso>
""".encode("utf-8")


class _FakeArchive:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {
            "finlex://sd-cons-old/1988/46/fin@20250001/main.xml": _consolidated_xml("19880046"),
            "finlex://sd-cons-old/1988/46/fin@20250001/media/46.gif": b"GIF89a",
            "finlex://sd-cons-old/1988/46/swe/main.xml": _consolidated_xml("19990001", lang="swe"),
            "finlex://sd-cons-old/1988/46/swe/media/46.gif": b"GIF89a",
        }
        self.stored: list[tuple[str, bytes, str]] = []

    def locators(self, pattern: str) -> list[str]:
        if pattern == "finlex://sd-cons-old/%/main.xml":
            return [
                "finlex://sd-cons-old/1988/46/fin@20250001/main.xml",
                "finlex://sd-cons-old/1988/46/swe/main.xml",
            ]
        if pattern.endswith("/%"):
            prefix = pattern[:-2]
            return sorted(
                locator for locator in self._data if locator.startswith(prefix + "/")
            )
        return sorted(locator for locator in self._data if pattern in locator)

    def get(self, locator: str) -> bytes | None:
        return self._data.get(locator)

    def has(self, locator: str) -> bool:
        return locator in self._data

    def store(self, locator: str, payload: bytes, storage_class: str = "xml") -> None:
        self._data[locator] = payload
        self.stored.append((locator, payload, storage_class))

    def close(self) -> None:
        return None


def test_backfill_migrates_sd_cons_old_to_canonical_versioned_sd_cons(monkeypatch) -> None:
    archive = _FakeArchive()
    monkeypatch.setattr(backfill, "Farchive", lambda db: archive)

    stats = backfill.run(db=Path("data/finlex.farchive"), dry_run=False, verbose=False)

    assert stats.errors == 0
    assert stats.locators_backfilled == 4
    assert all(locator.startswith("finlex://sd-cons/") for locator, _payload, _storage_class in archive.stored)
    assert {
        locator
        for locator, _payload, _storage_class in archive.stored
    } == {
        "finlex://sd-cons/1988/46/fin@19880046/main.xml",
        "finlex://sd-cons/1988/46/fin@19880046/media/46.gif",
        "finlex://sd-cons/1988/46/swe@19990001/main.xml",
        "finlex://sd-cons/1988/46/swe@19990001/media/46.gif",
    }
