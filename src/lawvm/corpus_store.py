"""Corpus locator helpers and archive-backed corpus access.

The shared store layer exposes Finlex-style locator construction plus a
versioned archive reader for consolidated artifacts. Finland uses the
TransparentCorpusStore path via ``get_corpus_store()``; ArchiveCorpusStore is
the strict read-only archive adapter for other corpus consumers.
"""

from __future__ import annotations
from typing_extensions import override

import hashlib
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from lawvm.core.source_witness import DigestWitness, SourceWitness

if TYPE_CHECKING:
    from farchive import Farchive
    from lawvm.core.stage_result import StageResult


def _read_with_content_witness(
    data: bytes | None,
    sid: str,
    source_role: str,
) -> tuple[bytes, SourceWitness] | None:
    """Pair source bytes with a content-addressed :class:`SourceWitness`.

    The sha256 ``DigestWitness`` is computed from the ACTUAL bytes (never from
    ``sid``), so two reads agree iff their bytes agree. Returns None for an
    absent read (preserving the ``read_source`` contract).
    """
    if data is None:
        return None
    witness = SourceWitness(
        source_role=source_role,
        artifact_id=sid,
        digest=DigestWitness(
            digest_algorithm="sha256", digest=hashlib.sha256(data).hexdigest()
        ),
    )
    return data, witness

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


def validate_farchive_create_path(path: Path) -> None:
    """Reject ambiguous farchive creation targets such as ``unused``."""
    if path.suffix != ".farchive":
        raise ValueError(
            f"refusing to create extensionless farchive destination: {path}; "
            "use a .farchive path"
        )


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

    # ------------------------------------------------------------------
    # Content-addressed / staged read surface (StageResult WAIST #1)
    # ------------------------------------------------------------------

    def read_source_witness(
        self, sid: str
    ) -> "tuple[bytes, SourceWitness] | None":
        """Source bytes paired with a content-addressed witness (or None).

        The witness carries a sha256 ``DigestWitness`` over the ACTUAL bytes
        (never derived from ``sid``). Default implementation wraps
        :meth:`read_source`; backends may override.
        """
        return _read_with_content_witness(
            self.read_source(sid), sid, "amendment_source_xml"
        )

    def read_amendment_witness(
        self, sid: str
    ) -> "tuple[bytes, SourceWitness] | None":
        """Amendment bytes paired with a content-addressed witness (or None)."""
        return _read_with_content_witness(
            self.read_amendment(sid), sid, "amendment_source_xml"
        )

    def read_source_staged(self, sid: str) -> "StageResult[bytes] | None":
        """Read enacted source XML as a typed :class:`StageResult` (or None).

        Carries the content witness as ``evidence``; the value is byte-identical
        to :meth:`read_source`. Backends with a source-acquisition policy
        (e.g. the Finland store) override to also attach a bundle admission.
        """
        from lawvm.core.stage_result import EvidenceBundle, StageResult

        witnessed = self.read_source_witness(sid)
        if witnessed is None:
            return None
        data, witness = witnessed
        return StageResult(value=data, evidence=EvidenceBundle((witness,)))

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


# ---------------------------------------------------------------------------
# Path resolution + fail-loud corpus-archive guard
# ---------------------------------------------------------------------------

# A freshly init_schema'd Farchive is a ~61 KB SQLite stub. The real corpora
# are hundreds of MB to multiple GB. Anything below this floor is treated as a
# stub/empty archive even before we open it. (Do not hardcode the exact stub
# size — it drifts with schema; this is a generous "clearly not a real corpus"
# floor.)
_MIN_POPULATED_ARCHIVE_BYTES = 1_000_000


class CorpusArchiveMissingError(RuntimeError):
    """Raised when a read/cache-only open targets a missing or stub corpus.

    The message embeds the literal token ``FARCHIVE_EMPTY_CORPUS`` so the
    failure is greppable and never silently degrades into "statute not found".
    """


def _repo_root() -> Path:
    """Repo root derived from this module's location (src/lawvm/corpus_store.py)."""
    return Path(__file__).resolve().parents[2]


def resolve_farchive_path(
    name: str,
    *,
    explicit_env: str = "LAWVM_FARCHIVE_DB",
) -> tuple[Path, str]:
    """Resolve a corpus-archive path through a single precedence chokepoint.

    Precedence (highest first):
        1. ``$<explicit_env>`` — explicit file path override (used as-is).
           Defaults to ``LAWVM_FARCHIVE_DB`` (the finlex corpus); callers for
           other corpora pass their own var (e.g. ``LAWVM_HE_FARCHIVE_DB``).
        2. ``$LAWVM_CANONICAL_DATA_ROOT/data/<name>`` — canonical data checkout,
           set by scripts/setup_worktree_links.sh in git worktrees.
        3. ``<repo_root>/data/<name>`` — module-relative repo-root default
           (replaces the historical cwd-relative ``data/<name>``).

    Returns ``(resolved_path, precedence_rule)`` where ``precedence_rule`` is a
    short human-readable label naming which rule produced the path (used in the
    fail-loud diagnostic).
    """
    explicit = os.environ.get(explicit_env)
    if explicit:
        return Path(explicit), f"{explicit_env} (explicit file override)"

    canonical_root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if canonical_root:
        return (
            Path(canonical_root) / "data" / name,
            "LAWVM_CANONICAL_DATA_ROOT/data/" + name,
        )

    return _repo_root() / "data" / name, "repo-root data/" + name


def _archive_is_populated(path: Path) -> bool:
    """Cheap populated-corpus check: file exists and is above the stub floor.

    The size check is a single ``stat`` and reliably separates GB-scale real
    corpora from the ~61 KB ``init_schema`` stub without opening SQLite.
    """
    try:
        return path.stat().st_size >= _MIN_POPULATED_ARCHIVE_BYTES
    except OSError:
        return False


def _missing_corpus_message(name: str, path: Path, rule: str) -> str:
    resolved = path.resolve() if path.exists() or path.is_symlink() else path
    return (
        f"FARCHIVE_EMPTY_CORPUS: corpus archive '{name}' is missing or is an "
        f"empty/stub archive.\n"
        f"  resolved path : {resolved}\n"
        f"  precedence    : {rule}\n"
        f"  remedy        : in a git worktree, link the corpus with "
        f"`scripts/setup_worktree_links.sh`, or set LAWVM_CANONICAL_DATA_ROOT "
        f"to a checkout whose data/ holds the populated corpora "
        f"(or LAWVM_FARCHIVE_DB to an explicit corpus file)."
    )


def open_corpus_archive(
    name: str,
    *,
    allow_create: bool = False,
    writable: bool = False,
    explicit_env: str = "LAWVM_FARCHIVE_DB",
) -> tuple[Farchive, Path, str]:
    """Open a corpus archive through the resolver, fail-loud on missing/stub.

    The corpus is always required to already exist and be populated: a missing
    or stub (below the populated floor) archive raises
    :class:`CorpusArchiveMissingError` *before* touching Farchive (whose
    writable constructor would otherwise mkdir + init an empty stub and mask
    the failure as "statute not found").

    ``writable`` opens an *existing populated* corpus read-write (e.g. explicit
    live-refresh tooling that updates the corpus in place). It still fails loud
    on a missing/stub archive — it never creates one.

    ``allow_create`` is the only path that may create a new archive on disk
    (ingest/import tools). It bypasses the populated-floor guard and opens
    writable.

    Returns ``(archive, resolved_path, precedence_rule)``.
    """
    from farchive import Farchive

    path, rule = resolve_farchive_path(name, explicit_env=explicit_env)

    if allow_create:
        validate_farchive_create_path(path)
        return Farchive(path, readonly=False), path, rule

    if not _archive_is_populated(path):
        raise CorpusArchiveMissingError(_missing_corpus_message(name, path, rule))

    return Farchive(path, readonly=not writable), path, rule


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_corpus_store(*, readonly: bool = False) -> CorpusStore:
    """Return a Farchive-backed TransparentCorpusStore over the Finlex corpus.

    The corpus is expected to already be populated: this factory opens the
    finlex corpus for reading and NEVER creates it. A missing or stub archive
    raises :class:`CorpusArchiveMissingError` instead of silently materialising
    an empty SQLite stub (which previously masqueraded downstream as
    "statute X not found in corpus"). Ingest happens via the dedicated import
    tools, not through this factory.

    Path resolution goes through :func:`resolve_farchive_path` (precedence:
    ``LAWVM_FARCHIVE_DB`` → ``$LAWVM_CANONICAL_DATA_ROOT/data/finlex.farchive``
    → ``<repo_root>/data/finlex.farchive``).

    Environment variables:
        LAWVM_FARCHIVE_DB=path           — explicit Farchive file override
        LAWVM_CANONICAL_DATA_ROOT=dir    — canonical data checkout (worktrees)
        LAWVM_TRANSPARENT_VERBOSE=1      — enable verbose fetch logging
        LAWVM_TRANSPARENT_CACHE_ONLY=0   — opt into live refresh on explicit tooling paths

    ``readonly`` is retained for caller-intent clarity. When cache-only mode is
    active (the default) the corpus is opened read-only. The explicit live-
    refresh path (``LAWVM_TRANSPARENT_CACHE_ONLY=0`` with ``readonly=False``)
    opens the existing populated corpus writable so refreshed fetches persist —
    but, like every path here, it fails loud on a missing/stub corpus rather
    than creating one.
    """
    from lawvm.finland.transparent_store import TransparentCorpusStore

    verbose = os.environ.get("LAWVM_TRANSPARENT_VERBOSE", "") == "1"
    cache_only = os.environ.get("LAWVM_TRANSPARENT_CACHE_ONLY", "1") != "0"

    writable = not (readonly or cache_only)
    archive, _path, _rule = open_corpus_archive("finlex.farchive", writable=writable)
    return TransparentCorpusStore(
        archive=archive,
        cache_only=cache_only,
        verbose=verbose,
    )
