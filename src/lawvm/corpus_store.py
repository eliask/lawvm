"""Corpus locator helpers and archive-backed corpus access.

The shared store layer exposes Finlex-style locator construction plus a
versioned archive reader for consolidated artifacts. Finland uses the
TransparentCorpusStore path via ``get_corpus_store()``; ArchiveCorpusStore is
the strict read-only archive adapter for other corpus consumers.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol

from lawvm.finland.consolidated_artifacts import (
    build_canonical_consolidated_locator,
    build_consolidated_corrigendum_locator,
    build_consolidated_main_locator,
    build_versioned_consolidated_corrigendum_glob,
    build_versioned_consolidated_main_glob,
    parse_consolidated_corrigendum_locator,
    parse_versioned_consolidated_main_locator,
)


class ArchiveLike(Protocol):
    def get(self, url: str) -> bytes | None: ...
    def locators(self, pattern: str = "%") -> list[str]: ...
    def fetch(self, url: str, max_age_hours: float | None = None) -> bytes | None: ...
    def close(self) -> None: ...


def statute_url(sid: str, lang: str = "fin") -> str:
    """Canonical URL for source statute XML."""
    return f"finlex://sd/{sid}/{lang}/main.xml"


def oracle_url(sid: str, lang: str = "fin", version: str = "") -> str:
    """Canonical URL for consolidated (oracle) XML.

    Consolidated ``sd-cons`` locators are versioned-only. Callers must provide
    the embedded amendment-id tag (``YYYYNNNN``).
    """
    if not version:
        raise ValueError(f"versioned consolidated locator required for {sid}")
    return build_consolidated_main_locator(
        sid=sid,
        lang=lang,
        version_tag=version,
    )


def media_url(sid: str, filename: str, lang: str = "fin") -> str:
    """Canonical URL for media blob (GIF, PDF)."""
    return f"finlex://sd/{sid}/{lang}/media/{filename}"


_AKN_STATUTE_RE = re.compile(
    r'akn/fi/act/statute/(\d{4}/[^/]+)/([^/@]+)@([^/]*)/(.+)'
)
_AKN_CONSOL_RE = re.compile(
    r'akn/fi/act/statute-consolidated/(\d{4}/[^/]+)/([^/@]+)@([^/]*)/(.+)'
)
# Corrigenda in the consolidated ZIP live at the statute root without a
# lang@version segment: akn/fi/act/statute-consolidated/{sid}/media/corrigenda/{file}
_AKN_CONSOL_CORRIGENDUM_RE = re.compile(
    r'akn/fi/act/statute-consolidated/(\d{4}/[^/]+)/media/corrigenda/([^/]+\.pdf)'
)
_AKN_HE_RE = re.compile(
    r'akn/fi/doc/government-proposal/(\d{4}/[^/]+)/([^/@]+)@([^/]*)/(.+)'
)

# Filename prefix → language code (sk = suomi/Finnish, fs = Swedish)
_CORRIGENDUM_LANG: dict[str, str] = {"sk": "fin", "fs": "swe"}


def _corrigendum_lang_from_filename(filename: str) -> str | None:
    """Infer language from Finlex corrigendum filename prefix (sk=fin, fs=swe)."""
    prefix = filename[:2].lower()
    return _CORRIGENDUM_LANG.get(prefix)


def akn_path_to_url(akn_path: str) -> str | None:
    """Convert an AKN corpus path to its canonical finlex:// URL."""
    m = _AKN_STATUTE_RE.search(akn_path)
    if m:
        sid, lang, version, rest = m.groups()
        if version:
            return f"finlex://sd/{sid}/{lang}@{version}/{rest}"
        return f"finlex://sd/{sid}/{lang}/{rest}"

    m = _AKN_CONSOL_RE.search(akn_path)
    if m:
        sid, lang, version, rest = m.groups()
        if not version:
            return None
        if rest == "main.xml":
            return build_consolidated_main_locator(
                sid=sid,
                lang=lang,
                version_tag=version,
            )
        if rest.startswith("media/corrigenda/"):
            return build_consolidated_corrigendum_locator(
                sid=sid,
                lang=lang,
                version_tag=version,
                filename=Path(rest).name,
            )
        return build_canonical_consolidated_locator(
            sid=sid,
            lang=lang,
            version_tag=version,
            rest=rest,
        )

    # Version-agnostic corrigendum path: no lang@version segment in ZIP path.
    # Handled separately because _AKN_CONSOL_RE requires lang@version.
    m = _AKN_CONSOL_CORRIGENDUM_RE.search(akn_path)
    if m:
        sid, filename = m.groups()
        lang = _corrigendum_lang_from_filename(filename)
        if lang is None:
            return None  # unknown prefix, skip
        # Caller must supply version separately; without it we can't build a
        # canonical locator here. Return None so callers use the dedicated path.
        return None

    m = _AKN_HE_RE.search(akn_path)
    if m:
        sid, lang, version, rest = m.groups()
        if version:
            return f"finlex://he/{sid}/{lang}@{version}/{rest}"
        return f"finlex://he/{sid}/{lang}/{rest}"

    return None


def corrigendum_media_url(sid: str, filename: str, lang: str = "fin", version: str = "") -> str:
    """Canonical URL for consolidated corrigendum PDF media."""
    if not version:
        raise ValueError(f"versioned consolidated locator required for {sid}")
    return build_consolidated_corrigendum_locator(
        sid=sid,
        lang=lang,
        version_tag=version,
        filename=filename,
    )


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class CorpusStore(ABC):
    """Unified read access to a Finlex-style corpus."""

    @abstractmethod
    def read_source(self, sid: str) -> bytes | None:
        """Read original enacted statute XML for sid (e.g. '2002/738').

        Returns None if the statute is not present.
        """

    @abstractmethod
    def read_oracle(self, sid: str) -> bytes | None:
        """Read the best versioned consolidated/oracle XML for sid.

        Picks the highest-numbered PIT version (fin@YYYYNNNN) numerically.
        Unversioned consolidated locators are ignored. Returns None if no
        versioned oracle is available.
        """

    @abstractmethod
    def read_media(self, sid: str, filename: str) -> bytes | None:
        """Read media blob (GIF/PDF) for statute.  Returns None if absent."""

    @abstractmethod
    def read_corrigendum_media(self, sid: str, filename: str) -> bytes | None:
        """Read corrigendum PDF from the consolidated corpus. Returns None if absent."""

    @abstractmethod
    def list_statute_ids(self) -> list[str]:
        """All statute IDs present in the corpus (e.g. ['2002/738', ...])."""

    @abstractmethod
    def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
        """Return {sid -> best versioned oracle identifier} for all statutes.

        For ArchiveCorpusStore the value is the versioned canonical finlex:// URL.
        """

    def read_amendment(self, sid: str) -> bytes | None:
        """Read amendment act XML.

        Semantically distinct from read_source but physically identical —
        both live under akn/fi/act/statute/.  Provided for call-site clarity.
        """
        return self.read_source(sid)

    @abstractmethod
    def read_locator(self, locator: str) -> bytes | None:
        """Read a canonical corpus locator directly."""

    def close(self) -> None:
        """Release owned backend resources."""
        return None


# ---------------------------------------------------------------------------
# Backend: ArchiveCorpusStore
# ---------------------------------------------------------------------------

class ArchiveCorpusStore(CorpusStore):
    """Read-only corpus store backed by Farchive (SQLite + zstd).

    Thread-safe for reads (Farchive uses WAL mode and check_same_thread=False).
    """

    def __init__(self, archive: ArchiveLike) -> None:
        self._archive = archive

    # ------------------------------------------------------------------
    # CorpusStore interface
    # ------------------------------------------------------------------

    def read_source(self, sid: str) -> bytes | None:
        url = statute_url(sid)
        return self._archive.get(url)

    def read_locator(self, locator: str) -> bytes | None:
        return self._archive.get(locator)

    def read_oracle(self, sid: str) -> bytes | None:
        # Versioned-only canonical consolidated namespace: pick the highest
        # numeric PIT key present in sd-cons for this SID.
        pattern = build_versioned_consolidated_main_glob(sid=sid)
        versioned = self._archive.locators(pattern)

        best_data: bytes | None = None
        best_pit: int = -2  # sentinel below "no PIT" (-1)

        for url in versioned:
            parts = parse_versioned_consolidated_main_locator(url)
            if parts is None:
                continue
            pit_key = int(parts.version)
            if pit_key > best_pit:
                data = self._archive.get(url)
                if data is not None:
                    best_pit = pit_key
                    best_data = data

        if best_data is not None:
            return best_data
        return None

    def read_media(self, sid: str, filename: str) -> bytes | None:
        url = media_url(sid, filename)
        return self._archive.get(url)

    def read_corrigendum_media(self, sid: str, filename: str) -> bytes | None:
        pattern = build_versioned_consolidated_corrigendum_glob(
            sid=sid,
            filename=filename,
        )
        urls = self._archive.locators(pattern)
        best_data: bytes | None = None
        best_pit = -2
        for url in urls:
            parts = parse_consolidated_corrigendum_locator(url, filename=filename)
            if parts is None:
                continue
            pit_key = int(parts.version)
            if pit_key > best_pit:
                data = self._archive.get(url)
                if data is not None:
                    best_pit = pit_key
                    best_data = data
        return best_data

    def list_statute_ids(self) -> list[str]:
        urls = self._archive.locators("finlex://sd/%/fin/main.xml")
        sids: list[str] = []
        for url in urls:
            # finlex://sd/{year}/{num}/fin/main.xml
            m = re.match(r'finlex://sd/(\d{4}/[^/]+)/fin/main\.xml$', url)
            if m:
                sids.append(m.group(1))
        return sids

    def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
        """Return {sid -> best versioned oracle URL} for ArchiveCorpusStore."""
        urls = self._archive.locators(build_versioned_consolidated_main_glob())
        candidates: dict[str, tuple[int, str]] = {}
        for url in urls:
            parts = parse_versioned_consolidated_main_locator(url)
            if parts is None:
                continue
            pit_key = int(parts.version)
            prev = candidates.get(parts.sid)
            if prev is None or pit_key > prev[0]:
                candidates[parts.sid] = (pit_key, url)
        return {sid: v[1] for sid, v in candidates.items()}

    def close(self) -> None:
        self._archive.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_corpus_store(*, readonly: bool = False) -> CorpusStore:
    """Return a Farchive-backed TransparentCorpusStore.

    Farchive DB is created if absent. This is the Finland pipeline factory.

    Environment variables:
        LAWVM_FARCHIVE_DB=path           — path to Farchive DB (default: data/finlex.farchive)
        LAWVM_TRANSPARENT_VERBOSE=1      — enable verbose fetch logging
        LAWVM_TRANSPARENT_CACHE_ONLY=0   — opt into live refresh on explicit tooling paths
    """
    import os
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore

    farchive_path = Path(os.environ.get("LAWVM_FARCHIVE_DB", "data/finlex.farchive"))
    verbose = os.environ.get("LAWVM_TRANSPARENT_VERBOSE", "") == "1"
    cache_only = os.environ.get("LAWVM_TRANSPARENT_CACHE_ONLY", "1") != "0"

    archive_readonly = (readonly or cache_only) and farchive_path.exists()
    archive = Farchive(farchive_path, readonly=archive_readonly)
    return TransparentCorpusStore(
        archive=archive,
        cache_only=cache_only,
        verbose=verbose,
    )
