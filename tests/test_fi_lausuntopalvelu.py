"""Reproducible draft-HE acquisition lane (lausuntopalvelu / hankeikkuna).

Unit tests monkeypatch the network entirely (no HTTP, no committed PDFs): they
pin (1) content-addressed manifestation construction + caching from local bytes,
(2) the typed-raise discipline on failure, and (3) the documented URL templates
and best-effort ``resolve_dossier`` link scraping. One ``@pytest.mark.network``
test actually fetches a real portal page IF reachable and skips otherwise.

Discipline mirrored: a network/HTTP failure is a typed ``HeFetchError`` (never a
silent empty manifestation); ``resolve_dossier`` returns ``()`` honestly when
nothing documented is found rather than faking a URL.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.finland.source_document import lausuntopalvelu as lp

_SAMPLE_PDF = b"%PDF-1.4\n%draft HE luonnos sample bytes\n%%EOF\n"
_SAMPLE_URL = (
    "https://www.lausuntopalvelu.fi/FI/Proposal/"
    "DownloadProposalAttachment?proposalId="
    "4edc99f4-f554-4524-889a-759a99d806ef&attachmentId=16477"
)


def test_fetch_he_draft_builds_content_addressed_manifestation(monkeypatch, tmp_path):
    calls: list[str] = []

    def fake_get(url: str) -> bytes:
        calls.append(url)
        return _SAMPLE_PDF

    monkeypatch.setattr(lp, "_http_get", fake_get)

    man = lp.fetch_he_draft(_SAMPLE_URL, cache_dir=str(tmp_path))

    assert isinstance(man, SourceManifestation)
    assert man.source_role == "government_proposal_draft"
    assert man.locator == _SAMPLE_URL
    assert man.media_type == "application/pdf"
    assert man.source_bytes == _SAMPLE_PDF
    assert man.artifact_digest == hashlib.sha256(_SAMPLE_PDF).hexdigest()
    assert calls == [_SAMPLE_URL]

    # Content-addressed by sha256 into the cache dir.
    cached = Path(tmp_path) / f"{man.artifact_digest}.pdf"
    assert cached.exists()
    assert cached.read_bytes() == _SAMPLE_PDF
    # No leftover temp part-file.
    assert not any(p.name.endswith(".part") for p in Path(tmp_path).iterdir())


def test_fetch_he_draft_cache_dir_precedence_env(monkeypatch, tmp_path):
    monkeypatch.setattr(lp, "_http_get", lambda url: _SAMPLE_PDF)
    monkeypatch.setenv("LAWVM_HE_CACHE_DIR", str(tmp_path))

    man = lp.fetch_he_draft(_SAMPLE_URL)  # no explicit cache_dir -> env wins

    assert (Path(tmp_path) / f"{man.artifact_digest}.pdf").exists()


def test_fetch_he_draft_empty_url_raises(monkeypatch):
    monkeypatch.setattr(lp, "_http_get", lambda url: _SAMPLE_PDF)
    with pytest.raises(ValueError):
        lp.fetch_he_draft("")


def test_fetch_he_draft_network_failure_is_typed(monkeypatch, tmp_path):
    def boom(url: str) -> bytes:
        raise lp.HeFetchError("simulated network down")

    monkeypatch.setattr(lp, "_http_get", boom)
    with pytest.raises(lp.HeFetchError):
        lp.fetch_he_draft(_SAMPLE_URL, cache_dir=str(tmp_path))


def test_http_get_empty_body_is_typed(monkeypatch):
    class _Resp:
        status = 200

        def read(self) -> bytes:
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(lp.urllib.request, "urlopen", lambda *a, **k: _Resp())
    with pytest.raises(lp.HeFetchError):
        lp._http_get("https://www.lausuntopalvelu.fi/whatever")


def test_url_templates_documented_shapes():
    guid = "4edc99f4-f554-4524-889a-759a99d806ef"
    part = lp.PARTICIPATION_URL.format(proposal_id=guid)
    assert part.endswith(f"Participation?proposalId={guid}")
    dl = lp.DOWNLOAD_ATTACHMENT_URL.format(proposal_id=guid, attachment_id=16477)
    assert "DownloadProposalAttachment?proposalId=" in dl
    assert dl.endswith("attachmentId=16477")
    hanke = lp.HANKEIKKUNA_PROJECT_URL.format(tunnus="VM045:00/2026")
    assert hanke.endswith("hankkeet?tunnus=VM045:00/2026")


def test_resolve_dossier_guid_scrapes_download_links(monkeypatch):
    guid = "4edc99f4-f554-4524-889a-759a99d806ef"
    html = (
        "<html><body>"
        f'<a href="/FI/Proposal/DownloadProposalAttachment?proposalId={guid}&amp;attachmentId=16477">Liite 3.pdf</a>'
        f'<a href="/FI/Proposal/DownloadProposalAttachment?proposalId={guid}&amp;attachmentId=16478">Liite 4.pdf</a>'
        '<a href="/FI/Proposal/List">unrelated</a>'
        # duplicate to prove de-dup
        f'<a href="/FI/Proposal/DownloadProposalAttachment?proposalId={guid}&amp;attachmentId=16477">dup</a>'
        "</body></html>"
    ).encode("utf-8")

    captured: list[str] = []

    def fake_get(url: str) -> bytes:
        captured.append(url)
        return html

    monkeypatch.setattr(lp, "_http_get", fake_get)
    urls = lp.resolve_dossier(guid)

    # Fetched the participation page (GUID path), not the hankeikkuna path.
    assert captured == [lp.PARTICIPATION_URL.format(proposal_id=guid)]
    assert len(urls) == 2  # de-duplicated
    assert all(u.startswith(lp.LAUSUNTOPALVELU_HOST) for u in urls)
    assert all("DownloadProposalAttachment" in u for u in urls)
    assert "attachmentId=16477" in urls[0]
    assert "attachmentId=16478" in urls[1]


def test_resolve_dossier_tunnus_uses_hankeikkuna_page(monkeypatch):
    captured: list[str] = []

    def fake_get(url: str) -> bytes:
        captured.append(url)
        return b"<html><body>no documented links here</body></html>"

    monkeypatch.setattr(lp, "_http_get", fake_get)
    urls = lp.resolve_dossier("VM045:00/2026")

    # tunnus -> government project page; no documented links -> honest empty.
    assert captured == [lp.HANKEIKKUNA_PROJECT_URL.format(tunnus="VM045:00/2026")]
    assert urls == ()


def test_resolve_dossier_empty_raises(monkeypatch):
    monkeypatch.setattr(lp, "_http_get", lambda url: b"")
    with pytest.raises(ValueError):
        lp.resolve_dossier("")


# --------------------------------------------------------------------------- #
# Live network test — actually fetch a real portal page if reachable.         #
# NO committed PDF, NO absolute/dev path. Skips cleanly when offline.          #
# --------------------------------------------------------------------------- #
@pytest.mark.network
def test_resolve_dossier_live_lausuntopalvelu_guid():
    guid = "4edc99f4-f554-4524-889a-759a99d806ef"  # public API-principles consultation
    try:
        urls = lp.resolve_dossier(guid)
    except lp.HeFetchError as exc:
        pytest.skip(f"lausuntopalvelu unreachable: {exc}")

    # The real static page carries documented DownloadProposalAttachment links.
    # (Portal content can change; assert the SHAPE, not an exact count.)
    for u in urls:
        assert u.startswith(lp.LAUSUNTOPALVELU_HOST)
        assert "DownloadProposalAttachment" in u
        assert f"proposalId={guid}" in u
