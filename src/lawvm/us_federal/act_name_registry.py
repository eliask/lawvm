"""Deterministic popular-act-name -> originating-act-key registry.

The :class:`~lawvm.us_federal.table3.Table3Resolver` maps ``(originating-act key,
act-section) -> USC address`` over the whole OLRC Statutes-at-Large classification
table. But its in-corpus join misses structurally: Table III keys on the
ORIGINATING act, while the amendatory lowering boundary only knows the AMENDING
Public Law's section. The realized-coverage win needs the missing half — turning a
NAMED act citation in the amendment text ("Section 5 of the Securities Act of
1933", "the Social Security Act") into the originating act key Table III expects.

This module is that half: a frozen, deterministic registry

    popular act name (normalized) -> originating act key(s) in Table III vocabulary

GROUNDED, NOT GUESSED. Every mapping is extracted from the official OLRC USC USLM
release: each codified Act states its popular name in a "... may be cited as the
'NAME'" short-title statement (a codified "Short title" section, or a
``shortTitle`` note), and the enclosing ``<sourceCredit>``/note cites the
originating act (a modern ``/us/pl/{c}/{n}`` -> Table III key ``{c}-{n}``, or an
older ``/us/act/{date}/ch{num}`` -> Table III chapter key ``{num}``). Each pair is
checked against Table III at build time: the act MUST classify into the USC title
whose XML the statement lives in, or the pair is dropped (no cross-title guess).
The grounded pairs are frozen into ``generated/popular_name_registry.json``; this
module loads them.

DISCIPLINE (AGENTS.md §1.7 fail-loud, mirroring :class:`Table3Resolver`):

- a name grounding to TWO distinct act keys -> :attr:`ActNameStatus.AMBIGUOUS`
  (refused, both keys carried as witnesses — never a guessed single act);
- a name with no grounded mapping -> :attr:`ActNameStatus.UNMAPPED` (refused);
- a clean single mapping -> :attr:`ActNameStatus.RESOLVED`, carrying the witness
  (the originating act ref + the USC node the short-title statement documents).

The registry resolves only the NAME -> act-key step; the act-section is supplied
by the caller (the section cited alongside the act name) and the
``(act-key, act-section) -> USC address`` join stays with
:class:`Table3Resolver`. The two compose into the new act-name resolution lane in
:mod:`lawvm.us_federal.nonpositive`.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib import resources
from typing import Iterable

# Packaged frozen registry (built by the one-shot extractor from the OLRC USC
# USLM short-title statements, grounded vs Table III).
_REGISTRY_PACKAGE = "lawvm.us_federal.generated"
_REGISTRY_RESOURCE = "popular_name_registry.json"

# A trailing ", as amended"/"(as amended)" tail and a leading article are
# editorial decoration on a popular-name citation, not part of the name.
_AS_AMENDED_TAIL_RE = re.compile(r"[\s,(]*as amended[\s)]*$", re.IGNORECASE)
_LEADING_ARTICLE_RE = re.compile(r"^(?:the)\s+", re.IGNORECASE)


def normalize_act_name(name: str) -> str:
    """Normalize a popular act name to the registry's lookup key.

    Folds case, NFKD-strips diacritics, unifies curly quotes, collapses internal
    whitespace, and drops a leading "the" and a trailing ", as amended". Returns
    ``""`` for empty input (which never matches any registry row).
    """
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'").replace("‘", "'")
    s = re.sub(r"\s+", " ", s).strip()
    s = _AS_AMENDED_TAIL_RE.sub("", s).strip()
    s = _LEADING_ARTICLE_RE.sub("", s).strip()
    return s.lower()


class ActNameStatus(StrEnum):
    """Closed set of popular-name -> act-key resolution outcomes."""

    RESOLVED = "resolved"
    """The name grounds to exactly one originating act key."""

    AMBIGUOUS = "ambiguous"
    """The name grounds to several distinct act keys — refused (§1.7)."""

    UNMAPPED = "unmapped"
    """No grounded registry row matches this name."""


@dataclass(frozen=True, slots=True)
class ActNameWitness:
    """The grounded provenance of one popular-name -> act-key mapping.

    ``raw_name`` is the verbatim popular name as stated in the OLRC short-title
    statement; ``usc_title`` is the title whose XML carried it; ``usc_node`` is the
    USC node identifier the statement documents; ``origin_ref`` is the originating
    act/PL ref the mapping was read from. These ground the mapping in falsifiable
    source data (no fabricated entry has a witness).
    """

    act_key: str
    raw_name: str
    usc_title: str
    usc_node: str
    origin_ref: str


@dataclass(frozen=True, slots=True)
class ActNameResolution:
    """Typed, frozen carrier for one popular-name resolution."""

    status: ActNameStatus
    name: str
    act_key: str = ""
    witness: ActNameWitness | None = None
    candidates: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.status is ActNameStatus.RESOLVED and bool(self.act_key)


_UNMAPPED_NAME = ""


class PopularNameRegistry:
    """Deterministic popular-act-name -> originating-act-key registry.

    Built from the grounded short-title mappings (the packaged frozen JSON, or any
    iterable of decoded entry dicts). Keyed by normalized popular name; a name
    bound to several distinct act keys is held as an AMBIGUOUS refusal rather than
    collapsed to one.
    """

    def __init__(self, entries: Iterable[dict]) -> None:
        # normalized name -> {act_key -> witness}
        self._by_name: dict[str, dict[str, ActNameWitness]] = {}
        self.entry_count = 0
        for entry in entries:
            name = normalize_act_name(entry.get("name", ""))
            if not name:
                continue
            slot = self._by_name.setdefault(name, {})
            for act in entry.get("acts", ()):
                key = (act.get("act_key") or "").strip()
                if not key:
                    continue
                if key not in slot:
                    slot[key] = ActNameWitness(
                        act_key=key,
                        raw_name=act.get("raw_name", ""),
                        usc_title=str(act.get("usc_title", "")),
                        usc_node=act.get("usc_node", ""),
                        origin_ref=act.get("origin_ref", ""),
                    )
            self.entry_count += 1

    @classmethod
    def from_bytes(cls, data: bytes) -> PopularNameRegistry:
        """Build a registry from the frozen registry JSON bytes."""
        doc = json.loads(data.decode("utf-8"))
        return cls(doc.get("entries", ()))

    @property
    def name_count(self) -> int:
        return len(self._by_name)

    @property
    def ambiguous_count(self) -> int:
        return sum(1 for slot in self._by_name.values() if len(slot) > 1)

    def resolve(self, name: str) -> ActNameResolution:
        """Resolve a popular act name to its originating act key (or refuse).

        Exactly one grounded act key -> ``RESOLVED`` (carrying the witness).
        Several distinct keys -> ``AMBIGUOUS`` (refused, candidates surfaced). No
        grounded row -> ``UNMAPPED``. Never guesses a single act for an ambiguous
        or unknown name (§1.7).
        """
        norm = normalize_act_name(name)
        if not norm:
            return ActNameResolution(status=ActNameStatus.UNMAPPED, name=norm)
        slot = self._by_name.get(norm)
        if not slot:
            return ActNameResolution(status=ActNameStatus.UNMAPPED, name=norm)
        keys = sorted(slot)
        if len(keys) != 1:
            return ActNameResolution(
                status=ActNameStatus.AMBIGUOUS,
                name=norm,
                candidates=tuple(keys),
            )
        only = keys[0]
        return ActNameResolution(
            status=ActNameStatus.RESOLVED,
            name=norm,
            act_key=only,
            witness=slot[only],
            candidates=(only,),
        )

    def resolve_act_key(self, name: str) -> str:
        """Just the resolved act key, or ``""`` (incl. ambiguous/unknown)."""
        res = self.resolve(name)
        return res.act_key if res.resolved else _UNMAPPED_NAME


# ---------------------------------------------------------------------------
# Lazily-loaded default registry (packaged frozen JSON)
# ---------------------------------------------------------------------------


@cache
def load_default_act_name_registry() -> PopularNameRegistry | None:
    """Lazily build the default registry from the packaged frozen JSON.

    Returns ``None`` — never raises — when the resource is absent, so a build host
    without the generated data degrades to the existing resolve-or-refuse baseline
    rather than failing. Cached process-wide (the parse is tiny).
    """
    try:
        data = (
            resources.files(_REGISTRY_PACKAGE)
            .joinpath(_REGISTRY_RESOURCE)
            .read_bytes()
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None
    if not data:
        return None
    return PopularNameRegistry.from_bytes(data)


def reset_default_act_name_registry() -> None:
    """Clear the cached default registry (test isolation)."""
    load_default_act_name_registry.cache_clear()
