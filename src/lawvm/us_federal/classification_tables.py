"""OLRC PL-section -> USC-section classification tables.

The OLRC (Office of the Law Revision Counsel, uscode.house.gov) publishes
**classification tables** that map Public Law sections to U.S. Code
title/section addresses. These tables are the bridge Public-Law text ->
codified USC address: an amendatory instruction may target "section 101(a)
of Public Law 118-5" with no USC citation in the enacted text itself, and
the classification table records that PL 118-5 sec. 101(a) was classified
to (e.g.) 2 U.S.C. 901.

The OLRC HTML host is geo-blocked from the build host but is mirrored on
the Wayback Machine. This module fetches the tables via Wayback, parses
the whitespace-delimited rows into typed ``ClassificationEntry`` carriers,
and builds a ``ClassificationIndex`` that resolves a ``statute_id`` +
``pl_section`` pair to a :class:`LegalAddress`. Resolution handles:

- **exact** section match (``"122"`` -> entry with ``pl_section="122"``)
- **sub-section** match (``"122(a)"`` -> entry with ``pl_section="122"``)
- **range** match (``"261-270"`` and any integer inside it resolves to
  the range's USC target)

The resolver returns a :class:`LegalAddress` only when every matching
entry agrees on the USC target. If matches disagree (genuine ambiguity
per AGENTS.md sec. 1.7) or there is no match, ``resolve`` returns ``None``
and ``resolve_all`` exposes the full candidate set for the integration
phase to surface as a typed finding.

Anchor diagrams and the AGENTS.md contract this implements:

- sec. 1.1 (no silent target hijacking)   -- resolve never broadens scope.
- sec. 1.9 (typed carriers)               -- ``ClassificationEntry`` is a
                                              frozen, slotted dataclass.
- sec. 1.11/sec. 1.12 (no surface semantics past the typed waist) -- the
                                              parser is the single owner of
                                              row -> entry; resolve never
                                              re-scans raw HTML.
- sec. 2.10 (planes stay type-distinct)   -- the table is **evidence** on
                                              the source plane; promote it
                                              to a LegalAddress only via an
                                              explicit resolve call, never
                                              by raising a string.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from lawvm.core.ir import LegalAddress
from lawvm.us_federal.source_tree import usc_section_address

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Wayback Machine "wildcard timestamp" form: ``2024id_`` redirects to the
# nearest capture of the wrapped URL and the ``id_`` suffix returns the raw
# original bytes without the Wayback toolbar chrome.
_WAYBACK_PREFIX = "https://web.archive.org/web/2024id_/"
_OLRC_TABLE_URL_TEMPLATE = "http://uscode.house.gov/classification/tbl{congress}pl_{session}.htm"

# Either 1 (first session) or 2 (second session). The OLRC URL uses ordinal
# suffix form: tbl118pl_1st.htm, tbl118pl_2nd.htm, ...
_SESSION_ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}

_USER_AGENT = (
    "Mozilla/5.0 (compatible; LawVM/0.1; +https://lawvm.us; "
    "OLRC classification table scraper)"
)
_HTTP_TIMEOUT = 60  # seconds; Wayback can be slow on cold captures

# Regexes used by the row parser. Compiled at module scope per AGENTS.md
# sec. 2.4 (regex discipline: compile reused patterns once at module scope,
# bound quantifiers, no adjacent unbounded quantifiers).

# Row tokenizer: whitespace runs separate columns.
_WS_RE = re.compile(r"\s+")

# A PL number token: "118-2", "116-92" -- 3-digit congress, hyphen, number.
_PL_NUM_RE = re.compile(r"^\d{1,3}-\d{1,4}$")

# A USC title: 1-54 (Title 54 was enacted in 2014; the upper bound is loose
# because the OLRC may stage future titles).
_USC_TITLE_RE = re.compile(r"^\d{1,2}$")

# A USC section: int, or int+letter (e.g. "2432", "1011a", "78o-10").
_USC_SECTION_RE = re.compile(r"^\d+[a-zA-Z]?(?:-[a-zA-Z0-9]+)?$|^\d+[a-zA-Z]?$")

# A stat-page marker: bracketed footnote "[4]", page range "31-33", comma-
# separated pair "12, 13" (two tokens), or single page "12".
_STAT_BRACKET_RE = re.compile(r"^\[\d+\]$")
_STAT_RANGE_RE = re.compile(r"^\d+-\d+$")
_STAT_NUM_RE = re.compile(r"^\d+$")
_STAT_COMMA_TOKEN_RE = re.compile(r"^\d+,$")

# A range PL section: "261-270" (hyphenated numeric range).
_PL_RANGE_RE = re.compile(r"^(?P<lo>\d+)-(?P<hi>\d+)$")

# A sub-section suffix on a PL section: "(a)", "(a)(1)", "(1)(A)".
_PL_SUBSECTION_SUFFIX_RE = re.compile(r"^(?P<root>\d+)(?P<suffix>\(.*)$")

# statute_id parsing: "PL 118-5" -> congress=118, number=5. Tolerates the
# en-dash (U+2013) the OLRC/citableAs form uses as well as ASCII hyphen.
_STATUTE_ID_RE = re.compile(
    r"^\s*PL\s+(?P<congress>\d{1,3})\s*[\u2013-]\s*(?P<number>\d{1,4})\s*$"
)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def _session_url_session_token(session: int) -> str:
    """Map a numeric session to the OLRC URL token ("1st", "2nd", ...).

    The OLRC publishes one table per Congress/session pair at
    ``tbl{congress}pl_{session}.htm`` where ``session`` is the ordinal
    suffix form: 1 -> "1st", 2 -> "2nd". Sessions beyond the short
    known list follow standard English ordinal rules (21st, 22nd,
    23rd, 11th-13th).
    """

    suffix = _SESSION_ORDINAL.get(session)
    if suffix is not None:
        return suffix
    n = int(session)
    last_two = n % 100
    if 11 <= last_two <= 13:
        ordinal = "th"
    else:
        ordinal = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{ordinal}"


def fetch_classification_table(congress: int, session: int) -> str:
    """Fetch the OLRC classification table via the Wayback Machine.

    OLRC publishes one HTML file per Congress/session pair at
    ``http://uscode.house.gov/classification/tbl{congress}pl_{session}.htm``
    where ``session`` is an ordinal suffix ("1st", "2nd"). The build host
    is geo-blocked, so this helper always goes through Wayback's
    ``2024id_`` wildcard redirect, which resolves to the nearest capture
    of the same URL and returns the raw original bytes.

    ``session`` is the integer session number (``1`` or ``2``).

    Returns the raw HTML text decoded as UTF-8 (the OLRC pages are
    ASCII-only with HTML entities).

    Raises ``urllib.error.URLError`` (or a subclass such as
    ``HTTPError``) on transport failure; the caller is responsible for
    surfacing that as a typed acquisition diagnostic rather than silently
    swallowing it (per AGENTS.md sec. 1.10).
    """

    if congress <= 0:
        raise ValueError(f"congress must be positive: {congress!r}")
    if session <= 0:
        raise ValueError(f"session must be positive: {session!r}")

    session_token = _session_url_session_token(session)
    olrc_url = _OLRC_TABLE_URL_TEMPLATE.format(congress=int(congress), session=session_token)
    wayback_url = _WAYBACK_PREFIX + olrc_url

    req = urllib.request.Request(
        wayback_url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9, */*;q=0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310
        raw = resp.read()
    # OLRC pages are ASCII / Latin-1 with entity-encoded special chars.
    # Decode defensively: UTF-8 first (strict), fall back to Latin-1 which
    # never raises on byte sequences that fail UTF-8 decoding and is a
    # historical default for legacy HTML pages.
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClassificationEntry:
    """One row of an OLRC classification table.

    Each entry records that one Public Law section (``pl_section`` of
    Public Law ``pl_congress``-``pl_number``) was classified to
    ``usc_title`` U.S.C. ``usc_section``. Rows that classify multiple PL
    sections to the same USC target (e.g. comma-separated
    "101(a), (b)" both -> 2 U.S.C. 901) are split into one
    ClassificationEntry per PL section by the parser.

    ``description`` is the optional OLRC annotation ("nt new", "nt",
    "repealed", "tr fr", ...). It is carried verbatim for forensic use
    but does not affect resolution: the typed entry owns the USC address.
    """

    pl_congress: int
    pl_number: int
    pl_section: str
    usc_title: int
    usc_section: str
    description: str = ""


def _strip_one_paren_group(text: str) -> str:
    """Strip one trailing ``(X)`` parenthesised group.

    Returns the result unchanged when no trailing group is present (e.g.
    ``"261-270"`` -> ``"261-270"``, ``"101"`` -> ``"101"``). When the input
    carries a chain like ``"101(a)(1)"``, only the rightmost group(s)
    are removed by repeated application -- callers that want the full
    parent peel must call this in a loop and probe each intermediate
    level (``"101(a)(1)"`` -> ``"101(a)"`` -> ``"101"``).
    """

    out = text.rstrip()
    if not out.endswith(")"):
        return out
    open_idx = out.rfind("(")
    if open_idx <= 0:
        return out
    return out[:open_idx].rstrip()


def _split_pl_section(pl_section: str) -> list[str]:
    """Split a comma-separated PL section cell into individual sections.

    The OLRC cell may carry "101(a), (b)" meaning both PL 118-5 sec.
    101(a) and 101(b) classify to the same USC target. Each piece is
    normalised into a standalone section identifier:

    - "101(a), (b)" -> ["101(a)", "101(b)"]
    - "(a), (b)" (a leading-comma group, unlikely but seen in some OLRC
      cells) -> []  (we cannot recover the root; treated as unparsable
      and skipped by the caller via the empty return list)
    - "261-270"    -> ["261-270"]  (ranges are kept whole; the resolver
      expands them later)
    - "1"          -> ["1"]

    Returns the list of normalised sections; an empty list means the cell
    could not be split into a usable section identifier (rare).
    """

    cell = pl_section.strip()
    if not cell:
        return []
    # Normalise ", " so each comma-separated piece is a standalone token
    # we can inspect. "(b)" appears as a separate piece after a comma.
    pieces = [p.strip() for p in cell.split(",")]
    pieces = [p for p in pieces if p]
    if not pieces:
        return []

    # Determine the root section from the first piece. "101(a)" -> "101";
    # "261-270" -> "261-270" (kept whole); "1" -> "1".
    root_match = re.match(r"^(?P<root>\d+(?:-\d+)?|[a-zA-Z]+)", pieces[0])
    if root_match is None:
        # The first piece does not start with a section-like root; we
        # cannot recover meaning. This is the "undecidable" case and the
        # caller will skip emitting entries for this row.
        return []
    root = root_match.group("root")

    out: list[str] = []
    for idx, piece in enumerate(pieces):
        if idx == 0:
            # Keep the first piece verbatim: "101(a)" stays "101(a)",
            # "261-270" stays "261-270", "1" stays "1".
            out.append(piece)
            continue
        # A subsequent piece is a bare parenthesised continuation: "(b)"
        # applies the root's numeric prefix. "101(a), (b)" -> "101(b)".
        if piece.startswith("(") or re.match(r"^[a-zA-Z]$", piece):
            # Bare "(b)" or "b" continuation: prepend the numeric root.
            numeric_root_match = re.match(r"^(\d+)", root)
            if numeric_root_match is None:
                # Non-numeric root (range "261-270" -> "261"); we cannot
                # safely attach a continuation.
                continue
            numeric_root = numeric_root_match.group(1)
            if piece.startswith("("):
                out.append(f"{numeric_root}{piece}")
            else:
                out.append(f"{numeric_root}({piece})")
            continue
        # A subsequent piece that is itself a full section like "102(a)"
        # is appended verbatim.
        out.append(piece)
    return out


def _parse_row_tokens(
    tokens: list[str],
    congress: int,
    line_no: int,
) -> list[ClassificationEntry]:
    """Parse one already-tokenised classification-table row.

    Returns zero, one, or more entries:

    - Zero: the row does not match the OLRC column shape.
    - One: the simple case ("1" -> one USC address).
    - More than one: a comma-separated PL section cell expanded into
      multiple entries ("101(a), (b)" -> two entries with a shared USC
      target).

    Tokenisation contract: ``tokens`` are non-empty whitespace-split
    pieces of a single logical row. The parser is the single owner of
    the row -> entries transform per AGENTS.md sec. 1.12; no other
    phase may re-scan the raw row text.
    """

    # Find the PL number token (first run of "NNN-N" from index 2 onward;
    # the USC title/section occupy indices 0 and 1).
    pl_num_idx: int | None = None
    for idx in range(2, len(tokens)):
        if _PL_NUM_RE.match(tokens[idx]):
            pl_num_idx = idx
            break
    if pl_num_idx is None:
        # Not a classification row; the caller iterates line by line and
        # silently skips blanks/headers, which is sound because there is
        # nothing to own for those lines.
        return []

    usc_title_raw = tokens[0]
    usc_section_raw = tokens[1]
    if not _USC_TITLE_RE.match(usc_title_raw):
        return []
    if not _USC_SECTION_RE.match(usc_section_raw):
        return []
    usc_title = int(usc_title_raw)
    # Normalise USC section: keep verbatim as OLRC prints it (e.g. "1011a",
    # "78o-10").
    usc_section = usc_section_raw

    pl_num_token = tokens[pl_num_idx]
    pl_congress_str, pl_number_str = pl_num_token.split("-", 1)
    pl_congress = int(pl_congress_str)
    pl_number = int(pl_number_str)

    description = " ".join(tokens[2:pl_num_idx]).strip()
    rest_tokens = tokens[pl_num_idx + 1 :]

    if not rest_tokens:
        # Whole-PL classification (no PL section column; the entire PL
        # classifies to this USC target). Represent with pl_section="" so
        # the resolver's "any pl_section under (PL, n)" lookup can fall
        # back to it.
        return [
            ClassificationEntry(
                pl_congress=pl_congress,
                pl_number=pl_number,
                pl_section="",
                usc_title=usc_title,
                usc_section=usc_section,
                description=description,
            )
        ]

    # Peel the trailing stat page tokens from rest_tokens, leaving the
    # PL section tokens in the prefix. Stat page formats we recognise:
    #   "[4]"        -- 1 token, bracketed footnote.
    #   "31-33"      -- 1 token, page range.
    #   "12," "13"   -- 2 tokens, comma-separated page list ("12, 13").
    #   "12"         -- 1 token, single page (rare).
    pl_section_end = len(rest_tokens)
    if pl_section_end >= 2:
        last = rest_tokens[-1]
        second_to_last = rest_tokens[-2]
        # Comma-pair stat page "12, 13" -- two tokens. Must be probed
        # BEFORE the single-number case because "13" alone also matches
        # _STAT_NUM_RE.
        if (
            _STAT_COMMA_TOKEN_RE.match(second_to_last)
            and _STAT_NUM_RE.match(last)
        ):
            pl_section_end -= 2
        elif _STAT_BRACKET_RE.match(last):
            pl_section_end -= 1
        elif _STAT_RANGE_RE.match(last):
            pl_section_end -= 1
        elif _STAT_NUM_RE.match(last):
            # Only treat a trailing bare number as stat page when the
            # row actually has a PL section in front of it; otherwise
            # this lone number IS the PL section ("section 1" of PL N-M).
            pl_section_end -= 1
        # else: stat page is absent; pl_section_end stays put.
    elif pl_section_end == 1:
        last = rest_tokens[-1]
        if _STAT_BRACKET_RE.match(last):
            pl_section_end -= 1
        elif _STAT_RANGE_RE.match(last):
            pl_section_end -= 1
        # A lone trailing number is the PL section, not the stat page.

    pl_section_tokens = rest_tokens[:pl_section_end]
    pl_section_raw = " ".join(pl_section_tokens).strip()
    pl_sections = _split_pl_section(pl_section_raw)

    if not pl_section_raw and not pl_sections:
        # Empty PL section and empty stat page -- treat as whole-PL.
        return [
            ClassificationEntry(
                pl_congress=pl_congress,
                pl_number=pl_number,
                pl_section="",
                usc_title=usc_title,
                usc_section=usc_section,
                description=description,
            )
        ]

    entries: list[ClassificationEntry] = []
    for piece in pl_sections:
        entries.append(
            ClassificationEntry(
                pl_congress=pl_congress,
                pl_number=pl_number,
                pl_section=piece,
                usc_title=usc_title,
                usc_section=usc_section,
                description=description,
            )
        )
    if not entries:
        # The PL section cell was non-empty but _split_pl_section
        # could not recover a usable identifier (rare). Preserve the
        # raw text so the row stays visible to forensics rather than
        # silently dropping it.
        entries.append(
            ClassificationEntry(
                pl_congress=pl_congress,
                pl_number=pl_number,
                pl_section=pl_section_raw,
                usc_title=usc_title,
                usc_section=usc_section,
                description=description,
            )
        )
    _ = line_no  # line_no is reserved for future diagnostics; unpinned
    # here to keep the parser stable across OLRC format drift.
    return entries


def parse_classification_table(html: str, congress: int) -> list[ClassificationEntry]:
    """Parse the OLRC whitespace-delimited classification table HTML.

    The OLRC page is HTML but the actual rows are emitted as whitespace-
    delimited text in a ``<pre>``-like block (not as ``<table>``/``<tr>``
    elements). We therefore scan the page line-by-line, tokenise each
    line on whitespace, and identify rows by their column shape: the
    parser is the single owner of the raw text -> typed entry transform
    per AGENTS.md sec. 1.12 (no later phase may re-scan the HTML).

    ``congress`` is informational -- it is the Congress this table
    belongs to, used to disambiguate PL numbers when a row's own
    congress field is missing or malformed. In practice every OLRC row
    carries its own ``NNN-N`` PL number so ``congress`` is only a
    fallback.

    Returns the parsed entries for the table; the order matches the
    input HTML row order (stable for diffing).
    """

    if not html:
        return []
    # Strip HTML tags conservatively: the OLRC pages wrap rows in
    # <html><head>...</head><body>...<pre>...</pre>...</body>...</html>.
    # We do not need a full HTML parser (per task spec) -- a simple
    # <tag>-strip plus entity decode is sufficient for the pre block.
    # The parser owns row -> entry; nothing downstream re-scans tags.
    text = _HTML_TAG_RE.sub(" ", html)
    text = _HTML_ENTITY_RE.sub(_decode_entity, text)
    entries: list[ClassificationEntry] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        # Skip HTML boilerplate lines that survived tag stripping (e.g.
        # DOCTYPE, bare "td" fragments, etc.). Real rows begin with a
        # numeric USC title.
        first_token = _WS_RE.split(line, maxsplit=1)[0]
        if not _USC_TITLE_RE.match(first_token):
            continue
        tokens = _WS_RE.split(line)
        entries.extend(_parse_row_tokens(tokens, congress=congress, line_no=line_no))
    return entries


# Module-scope compiled regexes used only by ``parse_classification_table``.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITY_RE = re.compile(r"&(?:#(?P<num>\d+)|#x(?P<hex>[0-9a-fA-F]+)|(?P<name>[a-zA-Z]+));")

# Cover the small set of named HTML entities OLRC actually uses. Named
# entities not in this map fall through and are left verbatim, which is
# safer than decoding something incorrectly.
_NAMED_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
    "mdash": "\u2014",
    "ndash": "\u2013",
}


def _decode_entity(match: re.Match[str]) -> str:
    num = match.group("num")
    if num is not None:
        try:
            return chr(int(num))
        except (ValueError, OverflowError):
            return match.group(0)
    hexs = match.group("hex")
    if hexs is not None:
        try:
            return chr(int(hexs, 16))
        except (ValueError, OverflowError):
            return match.group(0)
    name = match.group("name")
    if name is not None:
        return _NAMED_ENTITIES.get(name.lower(), match.group(0))
    return match.group(0)


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _IndexKey:
    """Internal compound key: (pl_congress, pl_number, pl_section)."""

    pl_congress: int
    pl_number: int
    pl_section: str


class ClassificationIndex:
    """Resolves (statute_id, pl_section) -> LegalAddress.

    The index owns the **evidence** plane: every entry stored in it is a
    classification-table record, and a successful resolve is a typed
    promotion to the legal-state plane via the returned
    :class:`LegalAddress`. Per AGENTS.md sec. 2.10 this promotion is
    explicit: callers must call :meth:`resolve` to obtain authority, the
    index never injects a string into a downstream op.

    Ambiguity policy (AGENTS.md sec. 1.7): when multiple entries match a
    query and they disagree on the USC target, :meth:`resolve` returns
    ``None`` rather than picking one by accident. Callers that need to
    surface the candidate set for triage use :meth:`resolve_all`.

    Range policy: when a row records a PL section range like "261-270"
    classifying to a USC target, the index expands every integer in the
    range to its own key. Thus resolve("PL 118-5", "265") hits the
    expanded "265" key, while resolve("PL 118-5", "261-270") hits the
    original "261-270" key (kept alongside the expansion). Range
    expansion is bounded to 256 to guard against pathological OLRC cells.
    """

    _MAX_RANGE_EXPANSION = 256
    _MAX_RANGE_EXPANSION_WARN_THRESHOLD = 256

    def __init__(self, entries: Iterable[ClassificationEntry]) -> None:
        # Primary index: (congress, number, pl_section) -> list of
        # (USC title, USC section) pairs. Multiple entries under the same
        # key are kept so resolve can detect ambiguity.
        self._index: dict[_IndexKey, list[tuple[int, str]]] = {}
        # Whole-PL fallback index: (congress, number) -> list of USC
        # targets for entries with pl_section="" (the entire PL classifies
        # here).
        self._whole_pl_index: dict[tuple[int, int], list[tuple[int, str]]] = {}
        # Stash all entries for forensic introspection (resolve_all,
        # stats, diffs). Frozen at construction.
        self._entries: tuple[ClassificationEntry, ...] = tuple(entries)
        for entry in self._entries:
            self._add(entry)

    # -- mutation (construction-time only) -------------------------------

    def _add(self, entry: ClassificationEntry) -> None:
        target = (entry.usc_title, entry.usc_section)
        if entry.pl_section == "":
            key = (entry.pl_congress, entry.pl_number)
            self._whole_pl_index.setdefault(key, []).append(target)
            return
        # Range expansion: a row like "261-270" classifies to one USC
        # target; every section number in [261, 270] resolves to it.
        range_match = _PL_RANGE_RE.match(entry.pl_section)
        if range_match is not None:
            lo = int(range_match.group("lo"))
            hi = int(range_match.group("hi"))
            if lo > hi:
                # Inverted range; preserve the original key only and do
                # not expand (the OLRC edit is suspect; surface via the
                # raw key).
                self._index_set(
                    _IndexKey(entry.pl_congress, entry.pl_number, entry.pl_section),
                    target,
                )
                return
            span = hi - lo + 1
            if span <= self._MAX_RANGE_EXPANSION:
                for n in range(lo, hi + 1):
                    self._index_set(
                        _IndexKey(entry.pl_congress, entry.pl_number, str(n)),
                        target,
                    )
            # Always also keep the original "261-270" key so a literal
            # range lookup resolves too.
            self._index_set(
                _IndexKey(entry.pl_congress, entry.pl_number, entry.pl_section),
                target,
            )
            return
        self._index_set(
            _IndexKey(entry.pl_congress, entry.pl_number, entry.pl_section),
            target,
        )

    def _index_set(self, key: _IndexKey, target: tuple[int, str]) -> None:
        bucket = self._index.get(key)
        if bucket is None:
            self._index[key] = [target]
        elif target not in bucket:
            bucket.append(target)

    # -- queries ---------------------------------------------------------

    @staticmethod
    def parse_statute_id(statute_id: str) -> tuple[int, int] | None:
        """Parse a ``"PL 118-5"`` style statute ID into a (congress, number) pair.

        Returns ``None`` if the ID does not match the canonical form. The
        OLRC citableAs form uses an en-dash (U+2013); ASCII hyphen is
        accepted as well. Per AGENTS.md sec. 1.11, no later phase may
        re-derive this from a string; ``parse_statute_id`` is the single
        owner of the statute-id -> (congress, number) transform for this
        module.
        """

        if not statute_id:
            return None
        match = _STATUTE_ID_RE.match(statute_id)
        if match is None:
            return None
        return int(match.group("congress")), int(match.group("number"))

    def resolve_all(self, statute_id: str, pl_section: str) -> list[LegalAddress]:
        """Return every USC target that the classification table records.

        Order is stable (insertion order across the index for the given
        key). Deduplicated. An empty list means no match; a list with
        conflicting targets is the ambiguity surface that :meth:`resolve`
        refuses to flatten.
        """

        key_pair = self.parse_statute_id(statute_id)
        if key_pair is None:
            return []
        congress, number = key_pair

        seen: list[LegalAddress] = []
        seen_targets: set[tuple[int, str]] = set()

        def _consider(target: tuple[int, str]) -> None:
            if target in seen_targets:
                return
            seen_targets.add(target)
            seen.append(usc_section_address(target[0], target[1]))

        # 1. Exact pl_section key.
        for target in self._index.get(
            _IndexKey(congress, number, pl_section), ()
        ):
            _consider(target)

        # 2. Parent-section peel layer by layer: "122(a)(1)" -> "122(a)"
        #    -> "122". Each intermediate level is probed against the index
        #    in turn so that a sub-section with no exact entry can still
        #    resolve to its parent's USC target.
        if not seen_targets:
            peel = pl_section
            while True:
                peeled = _strip_one_paren_group(peel)
                if not peeled or peeled == peel:
                    break
                for target in self._index.get(
                    _IndexKey(congress, number, peeled), ()
                ):
                    _consider(target)
                if seen_targets:
                    break
                peel = peeled

        # 3. Whole-PL fallback: the entire PL classifies to one USC target.
        if not seen_targets:
            for target in self._whole_pl_index.get((congress, number), ()):
                _consider(target)

        return seen

    def resolve(self, statute_id: str, pl_section: str) -> LegalAddress | None:
        """Resolve a PL section to a USC address via the classification table.

        Returns the unique :class:`LegalAddress` every matching entry
        agrees on. Returns ``None`` when:

        - the statute_id is not parseable;
        - no entry matches (sub-section peel and whole-PL fallback also
          miss);
        - more than one distinct USC target matches -- this is genuine
          OLRC ambiguity, and the resolver refuses to pick by Python
          accident (AGENTS.md sec. 1.7). Use :meth:`resolve_all` for
          the full candidate set when surfacing this as a typed finding.

        Lookup ladder (first hit resolves; later stages run only on miss):

        1. Exact ``pl_section`` ("122" -> entry with pl_section="122").
        2. Parent-section peel ("122(a)" -> entry with pl_section="122").
        3. Whole-PL fallback (no PL section in the row; the entire PL
           classifies here).
        """

        candidates = self.resolve_all(statute_id, pl_section)
        if len(candidates) == 1:
            return candidates[0]
        return None

    # -- introspection --------------------------------------------------

    def entries(self) -> tuple[ClassificationEntry, ...]:
        """Return all source entries the index was built from (forensic)."""

        return self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def stats(self) -> dict[str, int]:
        """High-level counts for CLI summary output."""

        pl_keys: set[tuple[int, int]] = set()
        for entry in self._entries:
            pl_keys.add((entry.pl_congress, entry.pl_number))
        return {
            "entries": len(self._entries),
            "index_keys": len(self._index),
            "whole_pl_keys": len(self._whole_pl_index),
            "distinct_public_laws": len(pl_keys),
        }

    # -- serialization --------------------------------------------------

    def to_jsonable(self) -> dict[str, object]:
        """Project the index to a JSON-safe dict.

        The serialized form is the raw entry list (the construction input);
        the index itself is rebuilt by ``from_jsonable`` so the resolved
        state never leaks as a side-channel. This is a projection plane
        artifact (AGENTS.md sec. 2.10): it is re-derivable from the source
        tables and never treated as the legal-state source of truth.
        """

        return {
            "version": 1,
            "entries": [
                {
                    "pl_congress": e.pl_congress,
                    "pl_number": e.pl_number,
                    "pl_section": e.pl_section,
                    "usc_title": e.usc_title,
                    "usc_section": e.usc_section,
                    "description": e.description,
                }
                for e in self._entries
            ],
        }

    @classmethod
    def from_jsonable(cls, data: Mapping[str, Any]) -> ClassificationIndex:
        """Rebuild an index from a JSON-safe dict produced by :meth:`to_jsonable`.

        Validates the schema and the type of each field rather than
        trusting the dict shape (AGENTS.md sec. 1.10: a missing or
        malformed field becomes a loud error, not a silent default).
        """

        if not isinstance(data, Mapping):
            raise TypeError(f"classification index JSON must be an object, got {type(data).__name__}")
        version = data.get("version")
        if version != 1:
            raise ValueError(f"unsupported classification index version: {version!r}")
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            raise TypeError(
                f"classification index 'entries' must be a list, got {type(raw_entries).__name__}"
            )
        entries: list[ClassificationEntry] = []
        for idx, raw in enumerate(raw_entries):
            if not isinstance(raw, Mapping):
                raise TypeError(
                    f"classification index entry[{idx}] must be an object, got {type(raw).__name__}"
                )
            entries.append(
                ClassificationEntry(
                    pl_congress=_require_int(raw, "pl_congress", idx),
                    pl_number=_require_int(raw, "pl_number", idx),
                    pl_section=_require_str(raw, "pl_section", idx),
                    usc_title=_require_int(raw, "usc_title", idx),
                    usc_section=_require_str(raw, "usc_section", idx),
                    description=_require_str(raw, "description", idx, default=""),
                )
            )
        return cls(entries)


def _require_int(raw: Mapping[Any, Any], key: str, idx: int) -> int:
    val = raw.get(key)
    if not isinstance(val, int) or isinstance(val, bool):
        raise TypeError(
            f"classification index entry[{idx}].{key} must be int, got {type(val).__name__}"
        )
    return val


def _require_str(raw: Mapping[Any, Any], key: str, idx: int, *, default: str | None = None) -> str:
    val = raw.get(key, default)
    if val is None:
        return ""
    if not isinstance(val, str):
        raise TypeError(
            f"classification index entry[{idx}].{key} must be str, got {type(val).__name__}"
        )
    return val


# A frozen, empty index is occasionally useful for callers that want to
# express "no classification data available" without a None check on every
# site. Constructed via the empty-entries constructor.
EMPTY_INDEX: ClassificationIndex = ClassificationIndex([])
