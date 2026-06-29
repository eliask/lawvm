"""Finland archive-backed corpus store and finlex URL utilities.

Finland-specific locator helpers (`finlex://` URL construction, AKN path
conversion, `ArchiveCorpusStore` over the Finlex farchive corpus) live here.
The generic corpus-store ABC, protocol, path-resolver guards, and
`get_corpus_store()` factory remain in :mod:`lawvm.corpus_store`.

Backward-compat re-exports at the bottom of :mod:`lawvm.corpus_store` keep the
73 historical call sites working without import changes (AGENTS.md §4 —
frontends own their locality, generic core owns the shared waist).
"""

from __future__ import annotations
from typing_extensions import override

import re
from pathlib import Path

from lawvm.corpus_store import ArchiveLike, CorpusStore
from lawvm.finland.consolidated_artifacts import (
    build_canonical_consolidated_locator,
    build_consolidated_corrigendum_locator,
    build_consolidated_main_locator,
    build_versioned_consolidated_corrigendum_glob,
    build_versioned_consolidated_main_glob,
    parse_consolidated_corrigendum_locator,
    parse_versioned_consolidated_main_locator,
)


# ---------------------------------------------------------------------------
# Finlex URL helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Finlex AKN regex recognizers
# ---------------------------------------------------------------------------

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


# Alias honoring the historical name used in some importer call sites; both
# names map to the same AKN→finlex conversion (single-source-of-truth).
akn_to_finlex_url = akn_path_to_url


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

    @override
    def read_source(self, sid: str) -> bytes | None:
        url = statute_url(sid)
        return self._archive.get(url)

    @override
    def read_locator(self, locator: str) -> bytes | None:
        return self._archive.get(locator)

    @override
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

    @override
    def read_media(self, sid: str, filename: str) -> bytes | None:
        url = media_url(sid, filename)
        return self._archive.get(url)

    @override
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

    @override
    def read_attachment_media(self, sid: str, filename: str) -> bytes | None:
        """Read attachment PDF — tries sd-cons (versioned) then sd/ (original)."""
        pattern = f"finlex://sd-cons/{sid}/fin@%/media/{filename}"
        urls = self._archive.locators(pattern)
        best_data: bytes | None = None
        best_pit = -2
        for url in urls:
            m = re.search(r"/fin@(\d+)/", url)
            if not m:
                continue
            pit_key = int(m.group(1))
            if pit_key > best_pit:
                data = self._archive.get(url)
                if data is not None:
                    best_pit = pit_key
                    best_data = data
        if best_data is not None:
            return best_data
        url = f"finlex://sd/{sid}/fin/media/{filename}"
        return self._archive.get(url)

    @override
    def list_statute_ids(self) -> list[str]:
        urls = self._archive.locators("finlex://sd/%/fin/main.xml")
        sids: list[str] = []
        for url in urls:
            # finlex://sd/{year}/{num}/fin/main.xml
            m = re.match(r'finlex://sd/(\d{4}/[^/]+)/fin/main\.xml$', url)
            if m:
                sids.append(m.group(1))
        return sids

    @override
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

    @override
    def close(self) -> None:
        self._archive.close()
