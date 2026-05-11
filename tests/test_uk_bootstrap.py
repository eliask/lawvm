from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.uk_legislation.bootstrap import _openapi_server_urls


def test_openapi_server_urls_extracts_server_url_metadata() -> None:
    urls = _openapi_server_urls(
        [{"url": "https://www.legislation.gov.uk"}, {"description": "missing url"}],
        source=Path("uk/openapi/spec.yaml"),
    )

    assert urls == ["https://www.legislation.gov.uk", ""]


def test_openapi_server_urls_rejects_non_object_entries() -> None:
    with pytest.raises(ValueError, match="non-object entries at indexes: 1, 2"):
        _openapi_server_urls(
            [{"url": "https://www.legislation.gov.uk"}, "silently-dropped-before", 42],
            source=Path("uk/openapi/spec.yaml"),
        )


def test_openapi_server_urls_rejects_non_array_servers_field() -> None:
    with pytest.raises(ValueError, match="servers field did not decode to a JSON array"):
        _openapi_server_urls({"url": "https://www.legislation.gov.uk"}, source=Path("uk/openapi/spec.yaml"))
