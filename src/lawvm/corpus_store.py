"""Corpus locator helpers and archive-backed corpus access.

The shared store layer exposes generic farchive path resolution, the
``CorpusStore`` ABC, the ``ArchiveLike`` protocol, and the
``get_corpus_store()`` factory. Finlex-specific locator construction
(``finlex://`` URL builders, AKN path conversion, ``ArchiveCorpusStore``) has
migrated to :mod:`lawvm.finland.archive_store`; this module re-exports those
names for backward compatibility so existing import sites remain unchanged
(Agents.md §4 — frontends own their locality, core owns the shared waist).
"""

from __future__ import annotations

import hashlib
import os
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


class ArchiveLike(Protocol):
    def get(self, url: str) -> bytes | None: ...
    def locators(self, pattern: str = "%") -> list[str]: ...
    def fetch(self, url: str, max_age_hours: float | None = None) -> bytes | None: ...
    def close(self) -> None: ...


class FarchivePathOutsideDataRoot(ValueError):
    """Raised when a farchive-create target resolves outside the data root.

    Per AGENTS.md §1.0/§1.1 (mutation boundary): the create-path validation
    must not silently widen to a SQLite file outside the resolved data root.
    The fields let triage point at the exact divergence (input, resolved,
    data root) without re-running the operation (AGENTS.md §1.10 — embed
    the load-bearing context, do not collapse to a generic message).

    The check is bypassed only when the resolved path matches an explicit
    ``LAWVM_*_FARCHIVE_DB`` operator override supplied through
    :func:`validate_farchive_create_path`'s ``explicit_env`` parameter —
    preserving the legitimate CLI/env override path while rejecting ``..``
    traversal, absolute path injection, and symlink targets that escape the
    data root.
    """

    def __init__(self, *, path: Path, resolved: Path, data_root: Path) -> None:
        self.path = path
        self.resolved = resolved
        self.data_root = data_root
        super().__init__(
            f"FarchivePathOutsideDataRoot: create target resolves outside the "
            f"data root.\n"
            f"  input path  : {path}\n"
            f"  resolved    : {resolved}\n"
            f"  data root   : {data_root}\n"
            f"  remedy      : pass a path under <data root>/<name>.farchive, "
            f"or set the appropriate LAWVM_*_FARCHIVE_DB env var to the "
            f"explicit target."
        )


def _data_root() -> Path:
    """Resolved canonical data root (mirrors :func:`resolve_farchive_path`).

    ``$LAWVM_CANONICAL_DATA_ROOT/data`` when the canonical override is set
    (worktree / shared data checkout); ``<repo_root>/data`` otherwise. The
    farchive-create path must resolve within this root unless an explicit
    ``LAWVM_*_FARCHIVE_DB`` operator override authorizes the divergence.
    """
    canonical_root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if canonical_root:
        return Path(canonical_root).resolve() / "data"
    return _repo_root() / "data"


def _is_within(path: Path, root: Path) -> bool:
    """Return True iff ``path`` is ``root`` itself or a descendant of it.

    Both arguments MUST be resolved (``Path.resolve()``) so symlink targets
    and ``..`` traversal are evaluated against the actual filesystem location
    rather than the surface notation (AGENTS.md §1.11 — surface predicate
    must not authorize state; the resolved path is the load-bearing check).
    """
    try:
        root.relative_to(root)
        path.relative_to(root)
    except ValueError:
        return False
    return path == root or root in path.parents


def _matches_explicit_farchive_env(
    resolved: Path, explicit_env: str | None
) -> bool:
    """True iff ``explicit_env`` is set and resolves to the same path.

    Operators may legitimately override the data root by pointing
    ``LAWVM_FARCHIVE_DB`` / ``LAWVM_HE_FARCHIVE_DB`` /
    ``LAWVM_US_FEDERAL_FARCHIVE_DB`` at an out-of-tree target (test
    fixtures, shared mounts, scratch space). When the supplied path equals
    such an explicit override it is treated as trusted operator input —
    not a path-traversal vector.
    """
    if not explicit_env:
        return False
    raw = os.environ.get(explicit_env)
    if not raw:
        return False
    try:
        return Path(raw).resolve() == resolved
    except OSError:
        return False


def validate_farchive_create_path(
    path: Path, *, explicit_env: str | None = None
) -> None:
    """Reject ambiguous farchive creation targets such as ``unused``.

    The suffix check (``.farchive``) is always enforced — it rejects
    extensionless scratch paths that would silently create untracked SQLite
    files (e.g. ``unused``).

    The data-root check is **opt-in via** ``explicit_env``: callers that
    resolve the path through a known precedence chain (default resolution
    via :func:`resolve_farchive_path`, which honours ``$LAWVM_*_FARCHIVE_DB``
    / ``$LAWVM_CANONICAL_DATA_ROOT`` / ``<repo_root>/data``) pass the env-var
    name so the resolved path is verified to lie within the data root OR
    match the operator override. Callers without an env-var precedence chain
    (CLI args, function parameters, Sweden's suffixless-guard path, NZ)
    keep the previous behaviour — suffix check only — preserving backwards
    compatibility while the new protection attaches to the default-resolved
    ingest path. This is purely additive: no caller regresses, and the
    default-resolved path (the production hot path) is now hardened against
    ``..`` traversal, absolute path injection, and symlink targets that
    escape the data root (Security M2). On divergence raises
    :class:`FarchivePathOutsideDataRoot` with the input, resolved, and
    data-root triplet so triage does not require re-running (AGENTS.md
    §1.10 — embed the load-bearing context, do not collapse to a generic
    message).
    """
    if path.suffix != ".farchive":
        raise ValueError(
            f"refusing to create extensionless farchive destination: {path}; "
            "use a .farchive path"
        )
    if explicit_env is None:
        return
    resolved = path.resolve()
    data_root = _data_root().resolve()
    if _is_within(resolved, data_root):
        return
    if _matches_explicit_farchive_env(resolved, explicit_env):
        return
    raise FarchivePathOutsideDataRoot(
        path=path, resolved=resolved, data_root=data_root
    )


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class CorpusStore(ABC):
    """Unified read access to a jurisdiction corpus.

    Generic abstract base shared across frontends; concrete backends live in
    each jurisdiction (e.g. :class:`ArchiveCorpusStore` and
    :class:`TransparentCorpusStore` for Finland).
    """

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
    def read_attachment_media(self, sid: str, filename: str) -> bytes | None:
        """Read attachment PDF from the statute's media folder. Returns None if absent."""

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

    def load_spine_base_ir(self, sid: str, base_ir: "object", base_xml_bytes: bytes):
        """PDF-spine base-loader fallback (FI PDF spine Phase 1).

        When the base ``main.xml`` body is an ``hcontainer``-only metadata
        wrapper (no operative ``SECTION``/``PARAGRAPH`` in ``base_ir``) AND a
        ``fin`` attachment PDF exists AND its statute-spine recogniser yields
        ``SECTION`` nodes, return a graftable base IR derived from that PDF —
        tagged as a distinct, LOWER-authority source lane so it never overrides
        a substantial XML base. Returns None otherwise (the common case: a
        substantial XML body is a hard non-fire).

        Lives on the store base class so both Finnish backends
        (``ArchiveCorpusStore`` / ``TransparentCorpusStore``) share the base
        load path keyed on ``finlex://sd/{y}/{n}/fin/main.xml`` without
        re-implementing it; the pure spine transform lives in
        :mod:`lawvm.finland.pdf_spine_base`.
        """
        from lawvm.core.ir import IRNode
        from lawvm.finland.pdf_spine_base import build_pdf_spine_base_ir

        assert isinstance(base_ir, IRNode)
        return build_pdf_spine_base_ir(self, sid, base_ir, base_xml_bytes)

    def load_spine_base_xml(
        self, sid: str, base_ir: "object", base_xml_bytes: bytes
    ) -> bytes | None:
        """AKN-XML view of the PDF-spine base (FI PDF spine Phase 2, Option B).

        Same gate as :meth:`load_spine_base_ir` (fires ONLY on an
        ``hcontainer``-only base with a §-structured attachment PDF; a
        substantial XML base is a hard non-fire), but serialises the spine IR to
        an AKN ``akomaNtoso`` document with the canonical Finlex
        ``part_N__chp_N__sec_N`` eId scheme and ``<section><num>N §</num>``
        heads. This lets the XML-based oracle / locator path
        (:class:`lawvm.finland.section_resolver.FinnishAKNResolver`) resolve
        against the PDF-derived base too — not only the IRNode ``.label`` graft.
        Returns None when no spine is materialised (identical fallback shape as
        :meth:`load_spine_base_ir`, so callers stay byte-identical off-path).
        """
        from lawvm.core.ir import IRNode
        from lawvm.finland.pdf_spine_base import (
            build_pdf_spine_base_ir,
            spine_ir_to_akn_xml_bytes,
        )

        assert isinstance(base_ir, IRNode)
        spine_ir = build_pdf_spine_base_ir(self, sid, base_ir, base_xml_bytes)
        if spine_ir is None:
            return None
        return spine_ir_to_akn_xml_bytes(spine_ir)

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
        validate_farchive_create_path(path, explicit_env=explicit_env)
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


# ---------------------------------------------------------------------------
# Backward-compat re-exports — Finland-specific implementations moved to
# ``lawvm.finland.archive_store``. The re-exports live at the bottom of this
# module (after `CorpusStore`, `ArchiveLike`, and the path-resolver helpers are
# fully defined) so the cross-module import here is unambiguous: when this
# module is imported, ``archive_store``'s `from lawvm.corpus_store import
# ArchiveLike, CorpusStore` reference finds those names already bound. The
# 73 historical import sites that read these names off ``lawvm.corpus_store``
# keep working without changes (AGENTS.md §4 — frontends own their locality,
# core owns the shared waist; the re-export pins the public seam).
# ---------------------------------------------------------------------------
from lawvm.finland.archive_store import (  # noqa: E402,F401
    ArchiveCorpusStore,
    akn_path_to_url,
    akn_to_finlex_url,
    corrigendum_media_url,
    media_url,
    oracle_url,
    statute_url,
)
