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

- USC oracle (IMPLEMENTED, annual edition htm): the govinfo USCODE annual
  edition title document used as a verification witness::

      us://usc/{year}/title{N}.htm                      (annual edition, htm)

  Content is KEYLESS from govinfo:
  ``https://www.govinfo.gov/content/pkg/USCODE-{year}-title{N}/html/USCODE-{year}-title{N}.htm``
  and is ``application/xhtml+xml`` (XHTML 1.0 Transitional, well-formed).
  Ingest via :mod:`lawvm.us_federal.import_usc`; parse via
  :mod:`lawvm.us_federal.source_tree`. See :func:`usc_annual_locator`.

- USC release point (RESERVED, not implemented): the OLRC USC release-point
  title XML::

      us://usc/release/pl{congress}-{num}/title{N}.xml  (release point)

  This release-point namespace is documented for a future oracle only:
  ``uscode.house.gov`` (OLRC) is geo-blocked, and the USLM-USC release points are
  OLRC-only. See ``reserved_usc_release_point_locator`` below and
  ``us/spec/SOURCE_STRATEGY.md``.

Content identity is the SHA-256 of the stored bytes (see :func:`content_digest`).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from lawvm.corpus_store import resolve_farchive_path, validate_farchive_create_path

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

# govinfo USCODE annual-edition title htm URL (keyless /content/pkg/ form). One
# document per (year, title); ``application/xhtml+xml``, well-formed XHTML 1.0.
GOVINFO_USCODE_HTM_URL = (
    "https://www.govinfo.gov/content/pkg/USCODE-{year}-title{title}/html/"
    "USCODE-{year}-title{title}.htm"
)
# Staged govinfo USCODE member filename (download artifact name).
GOVINFO_USCODE_MEMBER_NAME = "USCODE-{year}-title{title}.htm"

# Canonical USC oracle locator: us://usc/{year}/title{N}.htm
_USC_ANNUAL_LOCATOR_RE = re.compile(
    r"^us://usc/(?P<year>\d{4})/title(?P<title>\d+)\.htm$"
)
# Staged/govinfo member name: USCODE-2023-title11.htm
_USC_MEMBER_RE = re.compile(
    r"^USCODE-(?P<year>\d{4})-title(?P<title>\d+)\.htm$"
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
    readonly: bool = True,
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
        # Default-resolution path: apply the data-root check with the
        # explicit-env override channel so LAWVM_US_FEDERAL_FARCHIVE_DB
        # pointing at an out-of-tree target is honoured (operator trust).
        explicit_env = "LAWVM_US_FEDERAL_FARCHIVE_DB"
    else:
        path = Path(db_path)
        # Caller supplied the path directly (test fixture, ad-hoc ingest).
        # Caller is the operator-in-trust at this layer; pass explicit_env=None
        # so validate_farchive_create_path enforces only the suffix check
        # (Security M2 §4: opt-in via explicit_env, backwards-compatible).
        explicit_env = None

    if allow_create:
        validate_farchive_create_path(path, explicit_env=explicit_env)
        path.parent.mkdir(parents=True, exist_ok=True)
        return Farchive(path, readonly=False)
    return Farchive(path, readonly=readonly)


class _MissingDryRunFarchive:
    """Resolve-only archive facade for dry-run imports against missing DBs."""

    def resolve(self, _locator: str) -> None:
        return None

    def close(self) -> None:
        return None


def open_us_federal_import_farchive(
    db_path: Path | None = None,
    *,
    dry_run: bool = False,
) -> Farchive | _MissingDryRunFarchive:
    """Open the U.S. import archive without creating DBs during dry-runs."""
    if db_path is None:
        path, _rule = resolve_us_federal_farchive_path()
    else:
        path = Path(db_path)
    if dry_run:
        if path.exists():
            return open_us_federal_farchive(path, readonly=True)
        return _MissingDryRunFarchive()
    return open_us_federal_farchive(path, allow_create=True)


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
# USC oracle namespace (annual-edition htm)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UscAnnualIdentity:
    """Parsed identity of one USC annual-edition title document.

    Identifies a single govinfo USCODE annual edition (one ``year`` × one USC
    ``title``), the unit ingested at locator :pyattr:`locator`.
    """

    year: int
    title: int

    @property
    def locator(self) -> str:
        return usc_annual_locator(self.year, self.title)

    @property
    def member_name(self) -> str:
        return GOVINFO_USCODE_MEMBER_NAME.format(year=self.year, title=self.title)

    @property
    def source_url(self) -> str:
        return GOVINFO_USCODE_HTM_URL.format(year=self.year, title=self.title)


def usc_annual_locator(year: int, title: int) -> str:
    """Canonical USC oracle locator for one annual-edition title htm document."""
    return f"us://usc/{int(year)}/title{int(title)}.htm"


def parse_usc_annual_locator(locator: str) -> UscAnnualIdentity | None:
    """Parse a canonical ``us://usc/{year}/title{N}.htm`` locator, or None."""
    match = _USC_ANNUAL_LOCATOR_RE.match(locator.strip())
    if match is None:
        return None
    return UscAnnualIdentity(
        year=int(match.group("year")), title=int(match.group("title"))
    )


def parse_usc_member_name(name: str) -> UscAnnualIdentity | None:
    """Parse a staged ``USCODE-{year}-title{N}.htm`` member filename, or None."""
    match = _USC_MEMBER_RE.match(name.strip())
    if match is None:
        return None
    return UscAnnualIdentity(
        year=int(match.group("year")), title=int(match.group("title"))
    )


def usc_annual_locator_glob(year: int | None = None, title: int | None = None) -> str:
    """SQL-LIKE pattern for ``archive.locators`` over USC annual locators."""
    year_part = "%" if year is None else str(int(year))
    title_part = "%" if title is None else str(int(title))
    return f"us://usc/{year_part}/title{title_part}.htm"


def read_usc_annual(archive: UsArchiveReader, year: int, title: int) -> bytes | None:
    """Read one USC annual-edition title htm from the archive, or None."""
    return archive.get(usc_annual_locator(year, title))


def list_usc_annual_locators(
    archive: UsArchiveReader, year: int | None = None, title: int | None = None
) -> list[str]:
    """All USC annual locators present in the archive (optionally narrowed)."""
    return list(archive.locators(usc_annual_locator_glob(year, title)))


def list_usc_annual_identities(
    archive: UsArchiveReader, year: int | None = None, title: int | None = None
) -> list[UscAnnualIdentity]:
    """Typed identities for all USC annual editions present, sorted (year, title)."""
    identities: list[UscAnnualIdentity] = []
    for locator in list_usc_annual_locators(archive, year, title):
        identity = parse_usc_annual_locator(locator)
        if identity is not None:
            identities.append(identity)
    identities.sort(key=lambda i: (i.year, i.title))
    return identities


# Marker for the "current through" edition-currency comment in the USCODE htm
# header: ``<!-- SEARCHABLE-LAWS-ENACTED-THROUGH-DATE:January 3rd, 2024 -->`` and
# ``<!-- AUTHORITIES-LAWS-ENACTED-THROUGH-DATE:20240103 -->``.
_USC_ENACTED_THROUGH_RE = re.compile(
    rb"<!--\s*AUTHORITIES-LAWS-ENACTED-THROUGH-DATE:\s*(?P<date>\d{8})\s*-->"
)
_USC_ENACTED_THROUGH_TEXT_RE = re.compile(
    rb"<!--\s*SEARCHABLE-LAWS-ENACTED-THROUGH-DATE:\s*(?P<date>[^>]*?)\s*-->"
)
_USC_PUBLICATION_NAME_RE = re.compile(
    rb"<!--\s*AUTHORITIES-PUBLICATION-NAME:\s*(?P<name>[^>]*?)\s*-->"
)


def extract_usc_edition_currency(data: bytes) -> dict[str, str]:
    """Extract the edition-currency markers from a USCODE htm header.

    Returns a (possibly empty) mapping with any of ``laws_enacted_through``
    (``YYYYMMDD``), ``laws_enacted_through_text`` (human form), and
    ``publication_name`` that are present in the document header comments. These
    pin which Public Laws the edition incorporates (the witness denominator's
    upper bound). Absence is silent (returns no key) — the caller decides.
    """
    head = data[:8192]
    out: dict[str, str] = {}
    m = _USC_ENACTED_THROUGH_RE.search(head)
    if m is not None:
        out["laws_enacted_through"] = m.group("date").decode("ascii")
    m2 = _USC_ENACTED_THROUGH_TEXT_RE.search(head)
    if m2 is not None:
        out["laws_enacted_through_text"] = m2.group("date").decode("latin-1").strip()
    m3 = _USC_PUBLICATION_NAME_RE.search(head)
    if m3 is not None:
        out["publication_name"] = m3.group("name").decode("latin-1").strip()
    return out


# ---------------------------------------------------------------------------
# Reserved (not implemented): USC release-point namespace
# ---------------------------------------------------------------------------

# The USC release-point oracle is out of scope and blocked. This helper documents
# the reserved namespace and fails loud rather than pretend the oracle exists.

USC_ORACLE_BLOCKED_RULE_ID = "us_usc_oracle_unavailable"


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
