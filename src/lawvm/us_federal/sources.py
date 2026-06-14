"""U.S. federal farchive locator scheme and archive-backed resolution.

This module is the single source of truth for U.S. federal canonical locators
and for opening the U.S. federal farchive through the worktree/canonical-data
chokepoint.

Locator namespaces
------------------

- Amendment source (implemented): a single enacted Public Law, one govinfo
  bulkdata PLAW USLM XML member per law::

      us://plaw/{congress}/publ{N}.xml

  ``congress`` is the Congress number (e.g. ``118``) and ``N`` is the Public
  Law number within that Congress (e.g. ``5`` for Public Law 118-5). The govinfo
  bulkdata member is named ``PLAW-{congress}publ{N}.xml``.

  Private laws would be ``us://plaw/{congress}/pvtl{N}.xml`` but the public
  bulkdata zips contain no private-law members; acquisition filters to public.

- USC oracle (RESERVED, not implemented): the OLRC/govinfo USC release-point or
  annual-edition title XML used as a verification witness::

      us://usc/{year}/title{N}/...                      (annual edition)
      us://usc/release/pl{congress}-{num}/title{N}.xml  (release point)

  This namespace is documented for the future oracle only. The USC oracle is
  blocked here: ``uscode.house.gov`` (OLRC) is geo-blocked, and the govinfo
  USCODE collection needs an ``api.data.gov`` key. See ``reserved_usc_*`` below
  and ``us/spec/SOURCE_STRATEGY.md``.

Content identity is the SHA-256 of the stored bytes (see :func:`content_digest`).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from lawvm.corpus_store import resolve_farchive_path

if TYPE_CHECKING:
    from farchive import Farchive


class UsArchiveReader(Protocol):
    """Minimal archive surface the U.S. federal resolvers depend on.

    Narrower than :class:`lawvm.corpus_store.ArchiveLike` (which also requires
    ``fetch``): a plain ``Farchive`` satisfies this without a transparent-fetch
    layer, which is all the archive-first U.S. path needs.
    """

    def get(self, locator: str) -> bytes | None: ...
    def locators(self, pattern: str = "%") -> list[str]: ...


# Canonical farchive name for the U.S. federal corpus. Resolved through
# resolve_farchive_path so LAWVM_CANONICAL_DATA_ROOT / worktree links are honored.
US_FEDERAL_FARCHIVE_NAME = "us_federal.farchive"

# govinfo bulkdata PLAW URL form (one zip per Congress, USLM XML members).
GOVINFO_PLAW_ZIP_URL = (
    "https://www.govinfo.gov/bulkdata/PLAW/{congress}/public/"
    "PLAW-{congress}-public.zip"
)
GOVINFO_PLAW_MEMBER_URL = (
    "https://www.govinfo.gov/bulkdata/PLAW/{congress}/public/"
    "PLAW-{congress}publ{number}.xml"
)

# A zip member name: PLAW-118publ5.xml  (publ = public law). The public bulkdata
# zips contain only publ members; pvtl (private law) is matched for completeness
# so the acquisition layer can explicitly filter/record it.
_PLAW_MEMBER_RE = re.compile(
    r"^PLAW-(?P<congress>\d+)(?P<kind>publ|pvtl)(?P<number>\d+)\.xml$"
)

# Canonical amendment-source locator: us://plaw/{congress}/publ{N}.xml
_PLAW_LOCATOR_RE = re.compile(
    r"^us://plaw/(?P<congress>\d+)/(?P<kind>publ|pvtl)(?P<number>\d+)\.xml$"
)


@dataclass(frozen=True, slots=True)
class PlawMemberIdentity:
    """Parsed identity of one PLAW zip member or canonical locator.

    ``kind`` is ``"publ"`` (public law) or ``"pvtl"`` (private law). Acquisition
    keeps only public laws; ``is_public`` makes that filter explicit.
    """

    congress: int
    number: int
    kind: str

    @property
    def is_public(self) -> bool:
        return self.kind == "publ"

    @property
    def locator(self) -> str:
        return plaw_locator(self.congress, self.number, kind=self.kind)

    @property
    def member_name(self) -> str:
        return f"PLAW-{self.congress}{self.kind}{self.number}.xml"

    @property
    def public_law_label(self) -> str:
        """Human-facing label, e.g. 'Public Law 118-5' / 'Private Law 118-5'."""
        word = "Public Law" if self.is_public else "Private Law"
        return f"{word} {self.congress}-{self.number}"


def plaw_locator(congress: int, number: int, *, kind: str = "publ") -> str:
    """Canonical amendment-source locator for one Public (or Private) Law."""
    if kind not in ("publ", "pvtl"):
        raise ValueError(f"unknown PLAW kind: {kind!r} (expected 'publ' or 'pvtl')")
    return f"us://plaw/{int(congress)}/{kind}{int(number)}.xml"


def parse_plaw_member_name(name: str) -> PlawMemberIdentity | None:
    """Parse a PLAW zip member filename into a typed identity, or None."""
    match = _PLAW_MEMBER_RE.match(name.strip())
    if match is None:
        return None
    return PlawMemberIdentity(
        congress=int(match.group("congress")),
        number=int(match.group("number")),
        kind=match.group("kind"),
    )


def parse_plaw_locator(locator: str) -> PlawMemberIdentity | None:
    """Parse a canonical ``us://plaw/...`` locator into a typed identity."""
    match = _PLAW_LOCATOR_RE.match(locator.strip())
    if match is None:
        return None
    return PlawMemberIdentity(
        congress=int(match.group("congress")),
        number=int(match.group("number")),
        kind=match.group("kind"),
    )


def plaw_locator_glob(congress: int | None = None) -> str:
    """SQL-LIKE pattern for ``archive.locators`` over PLAW locators.

    With ``congress`` set, restrict to one Congress; otherwise all Congresses.
    """
    if congress is None:
        return "us://plaw/%/publ%.xml"
    return f"us://plaw/{int(congress)}/publ%.xml"


def content_digest(data: bytes) -> str:
    """SHA-256 content identity for stored U.S. federal source bytes."""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Archive resolution + open
# ---------------------------------------------------------------------------

def resolve_us_federal_farchive_path() -> tuple[Path, str]:
    """Resolve the canonical U.S. federal farchive path + precedence label.

    Mirrors ``import_zip.main()``'s ``resolve_farchive_path`` usage so the
    worktree/canonical-data-root precedence is identical to every other corpus.
    The archive need not exist yet — ingest tooling creates it via
    :func:`open_us_federal_farchive` with ``allow_create=True``.
    """
    return resolve_farchive_path(
        US_FEDERAL_FARCHIVE_NAME,
        explicit_env="LAWVM_US_FEDERAL_FARCHIVE_DB",
    )


def open_us_federal_farchive(
    db_path: Path | None = None,
    *,
    readonly: bool = False,
    allow_create: bool = False,
) -> Farchive:
    """Open the U.S. federal farchive.

    Resolution precedence (when ``db_path`` is None):
        1. ``$LAWVM_US_FEDERAL_FARCHIVE_DB`` explicit file override,
        2. ``$LAWVM_CANONICAL_DATA_ROOT/data/us_federal.farchive``,
        3. ``<repo_root>/data/us_federal.farchive``.

    ``allow_create`` permits ingest tooling to materialize a not-yet-existing
    archive (the only path that may create one). ``readonly`` opens an existing
    archive read-only for resolution/inventory consumers.
    """
    from farchive import Farchive

    if db_path is None:
        path, _rule = resolve_us_federal_farchive_path()
    else:
        path = Path(db_path)

    if allow_create:
        path.parent.mkdir(parents=True, exist_ok=True)
        return Farchive(path, readonly=False)
    return Farchive(path, readonly=readonly)


# ---------------------------------------------------------------------------
# Archive-backed resolution
# ---------------------------------------------------------------------------

def read_plaw(archive: UsArchiveReader, congress: int, number: int) -> bytes | None:
    """Read one Public Law USLM XML from the archive, or None if absent."""
    return archive.get(plaw_locator(congress, number))


def read_plaw_locator(archive: UsArchiveReader, locator: str) -> bytes | None:
    """Read a canonical ``us://plaw/...`` locator directly."""
    return archive.get(locator)


def list_plaw_locators(
    archive: UsArchiveReader, congress: int | None = None
) -> list[str]:
    """All PLAW locators present in the archive (optionally one Congress)."""
    return list(archive.locators(plaw_locator_glob(congress)))


def list_plaw_identities(
    archive: UsArchiveReader, congress: int | None = None
) -> list[PlawMemberIdentity]:
    """Typed identities for all PLAW units present, sorted (congress, number)."""
    identities: list[PlawMemberIdentity] = []
    for locator in list_plaw_locators(archive, congress):
        identity = parse_plaw_locator(locator)
        if identity is not None:
            identities.append(identity)
    identities.sort(key=lambda i: (i.congress, i.number))
    return identities


# ---------------------------------------------------------------------------
# Reserved (not implemented): USC oracle namespace
# ---------------------------------------------------------------------------

# The USC oracle is out of scope and blocked. These helpers document the
# reserved namespace and fail loud rather than pretend the oracle exists.

USC_ORACLE_BLOCKED_RULE_ID = "us_usc_oracle_unavailable"


def reserved_usc_annual_locator(year: int, title: int, rest: str = "") -> str:
    """Reserved logical locator for a USC annual-edition title artifact.

    NOT YET ACQUIRABLE. Documents the future oracle namespace only. The govinfo
    USCODE collection needs an ``api.data.gov`` key (out of scope here) and the
    USLM-vs-htm format / per-PL-vs-annual granularity is an open decision.
    """
    tail = f"/{rest.lstrip('/')}" if rest else ""
    return f"us://usc/{int(year)}/title{int(title)}{tail}"


def reserved_usc_release_point_locator(
    congress: int, public_law_number: int, title: int
) -> str:
    """Reserved logical locator for an OLRC USC release-point title XML.

    NOT YET ACQUIRABLE. ``uscode.house.gov`` (OLRC) is geo-blocked from here.
    """
    return (
        f"us://usc/release/pl{int(congress)}-{int(public_law_number)}/"
        f"title{int(title)}.xml"
    )
