"""OSC 8 terminal hyperlinks for human-readable LawVM command output.

This module wraps legislative reference tokens (HE / committee report / EV /
enacted statute) in OSC 8 escape sequences so capable terminals render them as
clickable links. It is used ONLY by the human-text renderers.

Hard correctness constraint: OSC 8 escapes must NEVER reach a non-TTY stream or
the structured/JSON output. The discharge bridge parses ``provision-state`` JSON
and users pipe/grep the human text, so an escape sequence in either path would
corrupt them. The gate is ``should_hyperlink()``; callers must consult it and
only wrap when it returns True. When off, the plain ref token is emitted
unchanged (byte-identical to the pre-feature output).

Verified URL templates (HE re-resolved 2026-06-13; the old /FI/vaski/*.aspx is dead):
  - HE (valtiopaivaasia):        https://www.eduskunta.fi/asiat-ja-aanestykset/valtiopaivaasiat/HE%20{n}%2F{year}%20vp
  - committee report / opinion:  https://www.eduskunta.fi/valtiopaivaasiakirjat/{TYPE}+{n}/{year}
  - EV (parliament response):    https://www.eduskunta.fi/valtiopaivaasiakirjat/EV+{n}/{year}
  - statute L {year}/{n}:        https://www.finlex.fi/fi/lainsaadanto/saadoskokoelma/{year}/{n}
  - consolidated statute (header id, ajantasainen):
                                 https://www.finlex.fi/fi/lainsaadanto/{year}/{n}

An unknown ref kind/type returns None from ``ref_url`` -> the caller emits plain
text. A missing link is fine; a wrong/404 link is not.
"""
from __future__ import annotations

import os
import re

# OSC 8 hyperlink: ESC ] 8 ; ; URL ST  text  ESC ] 8 ; ; ST   (ST = ESC backslash)
_OSC = "\033]8;;"
_ST = "\033\\"

# HE: the old /valtiopaivaasiat/HE+{n}/{year} now 301-redirects to this
# /asiat-ja-aanestykset/ path with the human "HE {n}/{year} vp" token URL-encoded
# (HE -> "HE%20{n}%2F{year}%20vp"). Point straight at the redirect target.
_EDUSKUNTA_ASIA = "https://www.eduskunta.fi/asiat-ja-aanestykset/valtiopaivaasiat"
# Committee reports / EV: the old /valtiopaivaasiakirjat/{TYPE}+{n}/{year} still
# resolves but redirects to an opaque edktunnus (EDK-YYYY-AK-NNNN) id we cannot
# construct, so it stays the canonical constructible form for these.
_EDUSKUNTA_ASIAKIRJA = "https://www.eduskunta.fi/valtiopaivaasiakirjat"
_FINLEX_SAADOS = "https://www.finlex.fi/fi/lainsaadanto/saadoskokoelma"
# Consolidated ("ajantasainen") version of a statute, keyed by bare YEAR/NUMBER
# (new Finlex SPA path; verified 200 for real consolidated statutes 2026-06-09).
_FINLEX_LAINSAADANTO = "https://www.finlex.fi/fi/lainsaadanto"

# Committee-report / committee-opinion prefixes whose mietinto/lausunto live in
# the valtiopaivaasiakirjat space. The TYPE goes into the URL verbatim.
_COMMITTEE_TYPES = frozenset(
    {
        "LaVM",
        "VaVM",
        "StVM",
        "HaVM",
        "PuVM",
        "SiVM",
        "TyVM",
        "YmVM",
        "TaVM",
        "MmVM",
        "LiVM",
        "UaVM",
        "PeVM",
        "TuVM",
        "TrVM",
        "PeVL",
        "VaVL",
        "StVL",
        "HaVL",
        "PuVL",
        "SiVL",
        "TyVL",
        "YmVL",
        "TaVL",
        "MmVL",
        "LiVL",
        "UaVL",
        "LaVL",
        "TuVL",
    }
)

HYPERLINK_MODES = ("auto", "always", "never")


def hyperlink(text: str, url: str) -> str:
    """Wrap ``text`` in an OSC 8 hyperlink pointing at ``url``.

    The visible text is unchanged; the URL is attached invisibly. Callers MUST
    have already passed the ``should_hyperlink`` gate -- this function does no
    gating itself.
    """
    return f"{_OSC}{url}{_ST}{text}{_OSC}{_ST}"


def ref_url(kind: str, n: int | str, year: int | str, type_prefix: str | None = None) -> str | None:
    """Return the verified eduskunta/Finlex URL for a reference, or None.

    kind:
      - "he"                  -> HE valtiopaivaasia
      - "committee_report"    -> mietinto/lausunto (needs ``type_prefix``, e.g. "LaVM")
      - "parliament_response" -> EV
      - "statute"             -> Finlex saadoskokoelma (``year`` then ``n``)

    Returns None for any unknown kind or unknown committee ``type_prefix`` so the
    caller degrades to plain text (a missing link is fine; a wrong one is not).
    """
    n_s = str(n)
    year_s = str(year)
    if not (n_s.isdigit() and year_s.isdigit()):
        return None
    if kind == "he":
        return f"{_EDUSKUNTA_ASIA}/HE%20{n_s}%2F{year_s}%20vp"
    if kind == "parliament_response":
        return f"{_EDUSKUNTA_ASIAKIRJA}/EV+{n_s}/{year_s}"
    if kind == "committee_report":
        if type_prefix is None or type_prefix not in _COMMITTEE_TYPES:
            return None
        return f"{_EDUSKUNTA_ASIAKIRJA}/{type_prefix}+{n_s}/{year_s}"
    if kind == "statute":
        # statute is L {year}/{n}: year is the path year, n the running number.
        return f"{_FINLEX_SAADOS}/{year_s}/{n_s}"
    return None


def should_hyperlink(mode: str, stream: object, *, is_json: bool = False) -> bool:
    """Decide whether OSC 8 escapes may be emitted.

    - JSON / structured output is ALWAYS plain (escapes there corrupt parsers).
    - mode "never" -> always plain; mode "always" -> always on (still not for JSON).
    - mode "auto"  -> on only when ``stream`` is a real TTY and TERM != "dumb".
    """
    if is_json:
        return False
    if mode == "never":
        return False
    if mode == "always":
        return True
    # auto
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty) or not isatty():
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return True


# --- ref-token parsing in the rendered human text -------------------------------

# committee/EV token: "LaVM 3/2026", "EV 23/2026"
_TOKEN_RE = re.compile(r"^(?P<prefix>[A-Za-z]+)\s+(?P<n>\d+)/(?P<year>\d{4})$")
# HE canonical id "he/2025/188"
_HE_CANON_RE = re.compile(r"^he/(?P<year>\d{4})/(?P<n>\d+)$")
# statute id "2026/269"
_STATUTE_RE = re.compile(r"^(?P<year>\d{4})/(?P<n>\d+)(?:-\w+)?$")


def he_url_from_canonical(he_id: str | None) -> str | None:
    """URL for an HE canonical id ``he/YEAR/NUMBER``; None if unparseable."""
    if not he_id:
        return None
    m = _HE_CANON_RE.match(he_id)
    if m is None:
        return None
    return ref_url("he", m.group("n"), m.group("year"))


def statute_url_from_id(statute_id: str | None) -> str | None:
    """URL for a statute id ``YEAR/NUMBER`` (the L token); None if unparseable.

    Points at the saadoskokoelma (the enacting/amending act as published).
    """
    if not statute_id:
        return None
    m = _STATUTE_RE.match(statute_id)
    if m is None:
        return None
    return ref_url("statute", m.group("n"), m.group("year"))


def consolidated_url_from_id(statute_id: str | None) -> str | None:
    """URL for the CONSOLIDATED ("ajantasa") version of a statute ``YEAR/NUMBER``.

    Used for the statute the provenance trace is ABOUT (the header id), which is
    the consolidated/in-force statute, not a single saadoskokoelma act. None if
    unparseable. Bare ``YEAR/NUMBER`` (no ``-`` suffix) only, to stay within the
    verified shape.
    """
    if not statute_id or "-" in statute_id:
        # Reject suffixed ids (e.g. "2026/269-x"); the consolidated key is bare.
        return None
    m = _STATUTE_RE.match(statute_id)
    if m is None:
        return None
    return f"{_FINLEX_LAINSAADANTO}/{m.group('year')}/{m.group('n')}"


def committee_url_from_raw(raw_text: str | None) -> str | None:
    """URL for a committee-report token like ``LaVM 3/2026``; None if unknown."""
    if not raw_text:
        return None
    m = _TOKEN_RE.match(raw_text.strip())
    if m is None:
        return None
    return ref_url("committee_report", m.group("n"), m.group("year"), type_prefix=m.group("prefix"))


def ev_url_from_raw(raw_text: str | None) -> str | None:
    """URL for an EV token like ``EV 23/2026``; None if unparseable."""
    if not raw_text:
        return None
    m = _TOKEN_RE.match(raw_text.strip())
    if m is None or m.group("prefix") != "EV":
        return None
    return ref_url("parliament_response", m.group("n"), m.group("year"))


def maybe_link(text: str, url: str | None, *, enabled: bool) -> str:
    """Return ``text`` wrapped in a hyperlink when ``enabled`` and ``url`` is set.

    The single convenience entry point for the renderers: when hyperlinks are
    off, or no URL could be derived, the plain ``text`` is returned unchanged.
    """
    if enabled and url:
        return hyperlink(text, url)
    return text
