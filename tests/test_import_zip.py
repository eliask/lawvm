from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import io
from pathlib import Path
import zipfile
from typing import Any, cast

from farchive import Farchive

from lawvm.tools import import_zip
from lawvm.tools.import_zip import import_consolidated_zip, import_statute_zip


@dataclass(frozen=True)
class _Span:
    status: str
    digest: str


@dataclass
class _StoreCall:
    locator: str
    data: bytes
    observed_at: datetime | None
    storage_class: str | None
    metadata: dict[str, Any] | None
    digest: str


@dataclass
class _FakeFarchive:
    current: dict[str, str] = field(default_factory=dict)
    calls: list[_StoreCall] = field(default_factory=list)

    def resolve(self, locator: str) -> _Span | None:
        digest = self.current.get(locator)
        if digest is None:
            return None
        return _Span(status="current", digest=digest)

    def store(
        self,
        locator: str,
        data: bytes,
        *,
        observed_at: datetime | None = None,
        storage_class: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        digest = hashlib.sha256(data).hexdigest()
        self.calls.append(
            _StoreCall(
                locator=locator,
                data=data,
                observed_at=observed_at,
                storage_class=storage_class,
                metadata=cast(dict[str, Any] | None, metadata),
                digest=digest,
            ),
        )
        self.current[locator] = digest
        return digest


class _FakeHTTPResponse:
    def __init__(self, data: bytes) -> None:
        self._bio = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._bio.read(size)

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


def _consolidated_xml(pit_version: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta>
      <identification>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="{pit_version}"/>
        </FRBRExpression>
      </identification>
    </meta>
    <body><section eId="sec_1"><num>1</num></section></body>
  </act>
</akomaNtoso>
""".encode("utf-8")


def _statute_xml() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta>
      <identification>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value=""/>
        </FRBRExpression>
      </identification>
    </meta>
    <body><section eId="sec_1"><num>1</num></section></body>
  </act>
</akomaNtoso>
"""


def _make_zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_import_consolidated_zip_uses_xml_version_for_all_stored_locators(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "consolidated.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "akn/fi/act/statute-consolidated/1988/46/fin@/media/corrigenda/46.gif",
            b"GIF89a",
        )
        zf.writestr(
            "akn/fi/act/statute-consolidated/1988/46/fin@/main.xml",
            _consolidated_xml("19880046"),
        )

    archive = cast(Any, _FakeFarchive())
    report = import_consolidated_zip(zip_path, archive, batch_size=1)

    assert report.total_errors == 0
    assert len(archive.calls) == 2
    gif_call = archive.calls[0]
    xml_call = archive.calls[1]
    assert gif_call.locator == "finlex://sd-cons/1988/46/fin@19880046/media/corrigenda/46.gif"
    assert "sd-cons-old" not in gif_call.locator
    assert gif_call.storage_class == "gif"
    assert gif_call.observed_at is None
    assert gif_call.data == b"GIF89a"
    assert gif_call.metadata is not None
    assert gif_call.metadata["source_url"] == str(zip_path)
    assert gif_call.metadata["source_surface"] == "statute-consolidated-zip"
    assert gif_call.metadata["entry_name"] == "akn/fi/act/statute-consolidated/1988/46/fin@/media/corrigenda/46.gif"
    assert gif_call.metadata["pit_version"] == "19880046"
    assert "zip_entry_mtime" in gif_call.metadata

    assert xml_call.locator == "finlex://sd-cons/1988/46/fin@19880046/main.xml"
    assert "sd-cons-old" not in xml_call.locator
    assert xml_call.storage_class == "xml"
    assert xml_call.observed_at is None
    assert xml_call.data == _consolidated_xml("19880046")
    assert xml_call.metadata is not None
    assert xml_call.metadata["source_url"] == str(zip_path)
    assert xml_call.metadata["source_surface"] == "statute-consolidated-zip"
    assert xml_call.metadata["entry_name"] == "akn/fi/act/statute-consolidated/1988/46/fin@/main.xml"
    assert xml_call.metadata["pit_version"] == "19880046"
    assert "zip_entry_mtime" in xml_call.metadata


def test_import_consolidated_zip_dry_run_does_not_store(tmp_path: Path) -> None:
    zip_path = tmp_path / "consolidated.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "akn/fi/act/statute-consolidated/1988/46/fin@/main.xml",
            _consolidated_xml("19880046"),
        )

    archive = cast(Any, _FakeFarchive())
    report = import_consolidated_zip(zip_path, archive, dry_run=True)

    assert report.total_errors == 0
    assert archive.calls == []


def test_import_consolidated_zip_keeps_each_family_root_identity(tmp_path: Path) -> None:
    zip_path = tmp_path / "consolidated.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "akn/fi/act/statute-consolidated/1988/46/fin@20200001/main.xml",
            _consolidated_xml("20200001"),
        )
        zf.writestr(
            "akn/fi/act/statute-consolidated/1988/46/fin@19990001/main.xml",
            _consolidated_xml("19990001"),
        )

    archive = cast(Any, _FakeFarchive())
    report = import_consolidated_zip(zip_path, archive)

    assert report.total_errors == 0
    assert [call.locator for call in archive.calls] == [
        "finlex://sd-cons/1988/46/fin@20200001/main.xml",
        "finlex://sd-cons/1988/46/fin@19990001/main.xml",
    ]


def test_import_statute_zip_accepts_url_and_streams_download(monkeypatch: Any) -> None:
    zip_bytes = _make_zip_bytes(
        {
            "akn/fi/act/statute/1988/46/fin@/main.xml": _statute_xml(),
        },
    )
    seen_urls: list[str] = []

    def fake_urlopen(req: Any, timeout: int = 0) -> _FakeHTTPResponse:
        seen_urls.append(getattr(req, "full_url", str(req)))
        return _FakeHTTPResponse(zip_bytes)

    monkeypatch.setattr(import_zip.urllib.request, "urlopen", fake_urlopen)
    archive = cast(Any, _FakeFarchive())
    report = import_statute_zip(
        "https://www.finlex.fi/api/assets/open-data/archives/statute.zip",
        archive,
    )

    assert report.total_errors == 0
    assert seen_urls == ["https://www.finlex.fi/api/assets/open-data/archives/statute.zip"]
    assert len(archive.calls) == 1
    call = archive.calls[0]
    assert call.locator == "finlex://sd/1988/46/fin/main.xml"
    assert call.storage_class == "xml"
    assert call.observed_at is None
    assert call.metadata is not None
    assert call.metadata["source_url"] == "https://www.finlex.fi/api/assets/open-data/archives/statute.zip"
    assert call.metadata["source_surface"] == "statute-zip"
    assert call.metadata["entry_name"] == "akn/fi/act/statute/1988/46/fin@/main.xml"


def test_import_consolidated_zip_accepts_url_and_streams_download(monkeypatch: Any) -> None:
    zip_bytes = _make_zip_bytes(
        {
            "akn/fi/act/statute-consolidated/1988/46/fin@/media/corrigenda/46.gif": b"GIF89a",
            "akn/fi/act/statute-consolidated/1988/46/fin@/main.xml": _consolidated_xml("19880046"),
        },
    )
    seen_urls: list[str] = []

    def fake_urlopen(req: Any, timeout: int = 0) -> _FakeHTTPResponse:
        seen_urls.append(getattr(req, "full_url", str(req)))
        return _FakeHTTPResponse(zip_bytes)

    monkeypatch.setattr(import_zip.urllib.request, "urlopen", fake_urlopen)
    archive = cast(Any, _FakeFarchive())
    report = import_consolidated_zip(
        "https://www.finlex.fi/api/assets/open-data/archives/statute-consolidated.zip",
        archive,
        batch_size=1,
    )

    assert report.total_errors == 0
    assert seen_urls == ["https://www.finlex.fi/api/assets/open-data/archives/statute-consolidated.zip"]
    assert len(archive.calls) == 2
    assert archive.calls[0].storage_class == "gif"
    assert archive.calls[0].locator == "finlex://sd-cons/1988/46/fin@19880046/media/corrigenda/46.gif"
    assert "sd-cons-old" not in archive.calls[0].locator
    assert archive.calls[0].metadata is not None
    assert archive.calls[0].metadata["source_surface"] == "statute-consolidated-zip"
    assert archive.calls[1].storage_class == "xml"
    assert archive.calls[1].locator == "finlex://sd-cons/1988/46/fin@19880046/main.xml"
    assert "sd-cons-old" not in archive.calls[1].locator


def test_import_statute_zip_warns_on_changed_locator(
    tmp_path: Path, capsys: Any
) -> None:
    zip_path = tmp_path / "statute.zip"
    xml = _statute_xml()
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("akn/fi/act/statute/1988/46/fin@/main.xml", xml)

    archive = _FakeFarchive()
    archive.current["finlex://sd/1988/46/fin/main.xml"] = hashlib.sha256(b"old").hexdigest()
    report = import_statute_zip(zip_path, archive)

    assert report.total_errors == 0
    err = capsys.readouterr().err
    assert "WARNING: finlex://sd/1988/46/fin/main.xml changed in statute.zip:" in err
    assert len(archive.calls) == 1
    assert archive.calls[0].observed_at is None


def test_import_consolidated_zip_warns_and_skips_duplicate_canonical_locator(
    tmp_path: Path, capsys: Any
) -> None:
    zip_path = tmp_path / "consolidated.zip"
    xml = _consolidated_xml("20250061")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "akn/fi/act/statute-consolidated/1961/302/fin@/main.xml",
            xml,
        )
        zf.writestr(
            "akn/fi/act/statute-consolidated/1961/302/fin@20250061/main.xml",
            xml,
        )

    archive = cast(Any, _FakeFarchive())
    report = import_consolidated_zip(zip_path, archive)

    assert report.total_errors == 0
    err = capsys.readouterr().err
    assert (
        "WARNING: duplicate logical locator finlex://sd-cons/1961/302/fin@20250061/main.xml "
        "in consolidated.zip: "
        "akn/fi/act/statute-consolidated/1961/302/fin@/main.xml -> "
        "akn/fi/act/statute-consolidated/1961/302/fin@20250061/main.xml; skipping later entry"
        in err
    )
    assert len(archive.calls) == 1
    assert archive.calls[0].locator == "finlex://sd-cons/1961/302/fin@20250061/main.xml"


def test_import_consolidated_zip_does_not_fail_on_existing_later_observation(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "consolidated.zip"
    xml = _consolidated_xml("19790658")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "akn/fi/act/statute-consolidated/1977/142/fin@/main.xml",
            xml,
        )

    archive = Farchive(tmp_path / "import_zip_out_of_order.farchive")
    locator = "finlex://sd-cons/1977/142/fin@19790658/main.xml"
    archive.store(
        locator,
        xml,
        observed_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        storage_class="xml",
        metadata={"source_surface": "api"},
    )

    report = import_consolidated_zip(zip_path, archive)

    assert report.total_errors == 0
    span = archive.resolve(locator)
    assert span is not None
    assert span.observation_count >= 2
