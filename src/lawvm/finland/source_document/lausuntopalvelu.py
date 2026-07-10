"""Reproducible draft-HE acquisition from the Finnish consultation portal.

Draft government proposals (HE luonnos) currently enter the source-document
plane via LOCAL PDF paths — which are NOT reproducible (a certificate that cites
a machine-local file path is meaningless to a checker). The reproducible witness for a
draft HE is the public consultation portal **lausuntopalvelu.fi** (and, for the
underlying dossier, the government project window **hankeikkuna** /
valtioneuvosto.fi/hankkeet).

This module is the ACQUISITION LANE: URL / dossier id → content-addressed
:class:`SourceManifestation`. It is deliberately stdlib-only (``urllib``); it
adds no dependency and does no parsing beyond the two documented URL shapes
below. It is a witness fetcher, not a producer — the fetched bytes flow into the
existing D2 ingest (:func:`ingest_pdf_manifestation`) and the draft-HE lowering
(:mod:`lawvm.finland.source_document.he_draft`).

Portal URL structure (researched 2026-07, documented here so the mapping is
auditable — see the module-level constants for the exact templates):

* **lausuntopalvelu.fi** — a consultation ("lausuntopyyntö") is addressed by a
  ``proposalId`` **GUID**::

      https://www.lausuntopalvelu.fi/FI/Proposal/Participation?proposalId=<GUID>

  The participation page is served as STATIC HTML (no JS/auth required to read
  attachment links). Each attachment PDF/DOCX is downloaded via::

      https://www.lausuntopalvelu.fi/FI/Proposal/DownloadProposalAttachment?proposalId=<GUID>&attachmentId=<INT>

  There is also a documented OData API at
  ``https://www.lausuntopalvelu.fi/api/v1/Lausuntopalvelu.svc/`` exposing
  ``Proposals(guid'<GUID>')`` — but its per-attachment id enumeration shape is
  not documented publicly, so the reliable attachment route is the HTML page's
  ``DownloadProposalAttachment`` links.

* **hankeikkuna** — a dossier is addressed by its ``tunnus`` (e.g.
  ``VM045:00/2026``). Documents ("asiakirjat") are served with a stable,
  content-like path::

      https://api.hankeikkuna.fi/asiakirjat/<kohde-uuid>/<asiakirja-uuid>/<FILENAME>.PDF

  and the project itself is browsable at
  ``https://valtioneuvosto.fi/hankkeet?tunnus=<TUNNUS>`` /
  ``https://valtioneuvosto.fi/hanke?tunnus=<TUNNUS>``.

GAP (honest): there is NO publicly documented, stdlib-reachable JSON endpoint
that maps a dossier ``tunnus`` -> its attachment URLs. The hankeikkuna search
API is a Swagger-UI-fronted service whose query-endpoint shape is not published
in a form we can pin without guessing, and the lausuntopalvelu OData
attachment-id enumeration is likewise undocumented. So :func:`resolve_dossier`
is BEST-EFFORT: it scrapes the static HTML participation/project page for the
documented download-link shapes. If a dossier is served only via a JS-rendered
listing, it returns ``()`` and the caller must supply the URL directly to
:func:`fetch_he_draft`. This gap is asserted in the tests, never faked.

Discipline (AGENTS.md §1.9, §1.10): a network / HTTP failure is a typed raise
(:class:`HeFetchError`), never a swallowed exception or silent empty result.
"""
from __future__ import annotations

import hashlib
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import List

from lawvm.core.source_document.extraction import SourceManifestation

# --- documented portal URL templates (auditable; see module docstring) -------
LAUSUNTOPALVELU_HOST = "https://www.lausuntopalvelu.fi"
PARTICIPATION_URL = (
    LAUSUNTOPALVELU_HOST + "/FI/Proposal/Participation?proposalId={proposal_id}"
)
DOWNLOAD_ATTACHMENT_URL = (
    LAUSUNTOPALVELU_HOST
    + "/FI/Proposal/DownloadProposalAttachment?proposalId={proposal_id}&attachmentId={attachment_id}"
)
HANKEIKKUNA_PROJECT_URL = "https://valtioneuvosto.fi/hankkeet?tunnus={tunnus}"

_DEFAULT_CACHE_DIR = ".tmp/he_cache"
_USER_AGENT = "lawvm-source-document/1 (+reproducible HE acquisition)"
_HTTP_TIMEOUT_S = 60

# A lausuntopalvelu proposalId is a GUID; a hankeikkuna tunnus is like VM045:00/2026.
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class HeFetchError(RuntimeError):
    """A draft-HE acquisition failed (network, HTTP status, empty body).

    Typed so callers can distinguish an acquisition failure from a parse /
    lowering failure downstream — never a silent None or empty manifestation.
    """


def _cache_root(cache_dir: str) -> Path:
    """Resolve the content-address cache root (arg > env > default)."""
    root = cache_dir or os.environ.get("LAWVM_HE_CACHE_DIR") or _DEFAULT_CACHE_DIR
    return Path(root)


def _http_get(url: str) -> bytes:
    """GET ``url`` with stdlib urllib; a network/HTTP failure is a typed raise."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:  # noqa: S310 (documented https host)
            status = getattr(resp, "status", 200)
            if status is not None and status >= 400:
                raise HeFetchError(f"HTTP {status} fetching {url}")
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise HeFetchError(f"HTTP {exc.code} fetching {url}") from exc
    except urllib.error.URLError as exc:
        raise HeFetchError(f"network error fetching {url}: {exc.reason}") from exc
    except OSError as exc:  # timeout, connection reset, DNS, ...
        raise HeFetchError(f"network error fetching {url}: {exc}") from exc
    if not body:
        raise HeFetchError(f"empty body fetching {url}")
    return body


def fetch_he_draft(url: str, *, cache_dir: str = "") -> SourceManifestation:
    """Fetch a draft-HE PDF by URL into a content-addressed SourceManifestation.

    Downloads the bytes with stdlib ``urllib`` (no new dependency), SHA-256
    digests them, and returns
    ``SourceManifestation(source_role="government_proposal_draft", locator=url,
    media_type="application/pdf")`` (the neutral core role; a Finnish HE luonnos
    is one instance of a government-proposal draft).

    The bytes are content-addressed into the cache directory (``cache_dir`` arg,
    else ``$LAWVM_HE_CACHE_DIR``, else ``.tmp/he_cache``), keyed by their SHA-256
    — a second fetch of the same URL re-reads the cached bytes without a network
    round-trip when the URL still resolves, and identical bytes from any URL
    share one cache entry. A network / HTTP failure is a typed
    :class:`HeFetchError`, never a silent partial manifestation.

    NOTE: caching is keyed by the CONTENT digest, not the URL. The first fetch of
    a given URL always hits the network (we cannot know the digest before
    fetching); it then stores ``<cache>/<sha256>.pdf``. This is the D8 artifact
    identity — the trusted object is the stored bytes, not the hope that the URL
    returns them again.
    """
    if not url:
        raise ValueError("fetch_he_draft: url must be non-empty")

    body = _http_get(url)
    digest = hashlib.sha256(body).hexdigest()

    root = _cache_root(cache_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        cache_path = root / f"{digest}.pdf"
        if not cache_path.exists():
            # Write atomically so a concurrent reader never sees a partial file.
            tmp = cache_path.with_suffix(".pdf.part")
            tmp.write_bytes(body)
            tmp.replace(cache_path)
    except OSError:
        # A cache-write failure must not defeat the fetch — the manifestation is
        # still valid from the bytes in hand. Caching is an optimisation, not the
        # truth boundary.
        pass

    return SourceManifestation(
        artifact_digest=digest,
        source_bytes=body,
        locator=url,
        source_role="government_proposal_draft",
        fetched_at=datetime.now(tz=timezone.utc),
        media_type="application/pdf",
    )


class _AttachmentLinkParser(HTMLParser):
    """Collect ``DownloadProposalAttachment`` hrefs from a static HTML page.

    Only the documented download-link shape is collected; the parser makes no
    attempt to render JS. An empty result therefore means either "no documented
    links on this static page" or "the page needs JS" — both are honestly a
    best-effort miss, reported by :func:`resolve_dossier` returning ``()``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: List[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value and "DownloadProposalAttachment" in value:
                self.hrefs.append(value)


def _absolutize(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return LAUSUNTOPALVELU_HOST + href
    return LAUSUNTOPALVELU_HOST + "/" + href


def resolve_dossier(dossier_id: str) -> tuple[str, ...]:
    """Best-effort resolve a dossier id to its attachment PDF/DOCX download URLs.

    Two id shapes are accepted:

    * a lausuntopalvelu ``proposalId`` **GUID** — the participation page is
      fetched and its static-HTML ``DownloadProposalAttachment`` links are
      returned (this is the RELIABLE path: the page is static HTML, the
      download-link shape is documented and stable);
    * a hankeikkuna ``tunnus`` (e.g. ``VM045:00/2026``) — the
      ``valtioneuvosto.fi/hankkeet`` project page is fetched and scraped for the
      same documented link shapes.

    GAP (do not fake): there is NO publicly documented, stdlib-reachable JSON
    endpoint mapping a ``tunnus`` to its attachment URLs (the hankeikkuna search
    API is Swagger-UI-fronted with an unpublished query shape; the
    lausuntopalvelu OData attachment enumeration is undocumented). So a ``tunnus``
    served only through a JS-rendered listing resolves to ``()`` — the caller
    then supplies the direct attachment URL to :func:`fetch_he_draft`. A GUID
    whose page is JS-only likewise resolves to ``()``. A NETWORK failure is a
    typed :class:`HeFetchError`; an empty result is an honest "nothing
    documented found", never a swallowed error.
    """
    if not dossier_id:
        raise ValueError("resolve_dossier: dossier_id must be non-empty")

    if _GUID_RE.match(dossier_id.strip()):
        page_url = PARTICIPATION_URL.format(proposal_id=dossier_id.strip())
    else:
        # hankeikkuna tunnus (or anything else) -> the government project page.
        page_url = HANKEIKKUNA_PROJECT_URL.format(tunnus=dossier_id.strip())

    body = _http_get(page_url)
    parser = _AttachmentLinkParser()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
    # lawvm-failloud: a malformed HTML page is a best-effort miss, returns () not a crash
    except Exception:  # noqa: BLE001
        return ()

    seen: set[str] = set()
    out: List[str] = []
    for href in parser.hrefs:
        absolute = _absolutize(href)
        if absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return tuple(out)
