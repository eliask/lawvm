"""The LOCUS snapshot adapter — a static-snapshot producer for the substrate.

This is the second producer of the distributable substrate (the first being the
FI engine-replay :mod:`lawvm.substrate.exporter`). It proves the substrate is
jurisdiction-neutral and that the uniform object model scales from the
*most-amended national code* (Rikoslaki, replay) down to the
*never-amended observed snapshot* (a US municipal code, here).

The two producers share the SAME object-model → manifest → pack assembly half
(``lawvm.substrate.{canonical_json,roots,selection,source,manifest,checker}`` +
the exporter's pure inline body-builders + its streaming ``_LayerWriter``). What
is different is the SOURCE-PRODUCER half:

* the FI exporter runs the replay engine and emits ``CertifiedTreeTransition`` /
  checkpoint trace rows across many change-dates;
* the LOCUS adapter reads a static snapshot (one observed point in time, no
  amendments), **induces** an address tree from the section header numbering,
  and emits ONE ``InitialStateEvent`` of genesis kind
  ``observed_codification_snapshot`` per work, with NO transitions.

Address induction (design §16, the LOCUS path). A LOCUS row's ``header`` carries
the section numbering, e.g. ``### 1.05.010 Name of municipality...`` →
``title:1 / chapter:05 / section:010``. The markdown ``#`` depth is NOT a
reliable structural signal (the same chapter mixes ``##`` and ``###``); the
dotted number is the authoritative skeleton. A header that does not yield a
multi-segment dotted address (a chapter/article TITLE heading like
``GENERAL PROVISIONS``, a subsection marker like ``(a)`` or ``1.``, a ``§``-style
ordinance number we do not yet model) is **never silently dropped** — it becomes
a TYPED residual (``locus_header_unparsed`` / ``locus_duplicate_address``) so the
pack stays totality-honest (every row owned or typed-residualized).

The 4 analytical scores + ``function`` / ``topic`` are LLM-derived ENRICHERS,
not legal-state matter. They go to the reserved ``overlay/`` layer as
``lawvm.overlay.v1`` rows anchored to ``content_leaf_hash`` (determinism
firewall, design §22.3 — they MUST NOT enter any legal-state / selection root).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, cast

from lawvm.substrate.canonical_json import JsonValue, nfc, wrap_row
from lawvm.substrate.exporter import (
    CANON_PROFILE,
    IDENTITY_ENCODING,
    STORAGE_CODEC,
    _LayerWriter,
    _address_node_body,
    _coverage_body,
    _git_commit,
    _residual_body,
    _struct_node_id,
    _work_body,
)
from lawvm.substrate.manifest import (
    PackManifest,
    PackProvenance,
)
from lawvm.substrate.roots import (
    leaf_hash,
    seq_root,
    set_root,
)
from lawvm.substrate.selection import (
    ApplicabilityFact,
    DecisionBasis,
    PROFILE_GOVERNING_TEXT,
    ScopePredicate,
    SelectionCandidate,
    SelectionCandidateSet,
    SelectionRow,
    SelectionUniverse,
    TemporalBasis,
    build_selection_index_roots,
    build_state_selection_roots,
    v0_profiles,
)
from lawvm.substrate.source import (
    Availability,
    GenesisKind,
    InitialStateEvent,
    Locator,
    LogicalKind,
    PriorHistoryStatus,
    SourceManifestation,
    SourceRecord,
)

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

PACK_KIND = "lawvm.pack.snapshot.v0"
JURISDICTION = "us-local"

# The observed-snapshot effective date. A LOCUS snapshot has no per-section
# effective dates (no amendment history); the whole codification is observed at
# one moment. We use a single fixed account/effect date so every selected row is
# constant over ``[SNAPSHOT_DATE, +inf)``. This is a corpus-account fact, not a
# legislative commencement claim.
SNAPSHOT_DATE = "2025-01-01"

# Schemas (reuse the exporter's where shared; overlay is local).
SCHEMA_OVERLAY = "lawvm.overlay.v1"

# Overlay leaf-hash domain (one per object family).
_DOMAIN_OVERLAY = "overlay"

# v0 single-branch / single-profile selection axis (mirrors the exporter).
_BRANCH_ID = "actual"
_PROFILE_ID = PROFILE_GOVERNING_TEXT
_RAIL_PERMANENT = "permanent"

# The four analytical-score columns (LLM enrichers — overlay matter, NOT legal
# state). Carried as overlay payload anchored to the content leaf.
_SCORE_COLUMNS: tuple[str, ...] = (
    "enforcement_discretion",
    "opacity",
    "paternalism",
    "problem_salience",
)

# Header → address induction. Strip leading markdown ``#`` then require a
# MULTI-segment dotted number (>= 2 segments, i.e. >= 1 dot) as the address
# skeleton. A bare single number (``1``, ``2``) is a list/article marker, never
# a section address (verified against the corpus — bare ``1`` collides), so it
# residualizes rather than producing a phantom address.
_MD_HEADING = re.compile(r"^#+\s*")
# A leading section-sign / ``Sec.`` / ``Section`` label that precedes the actual
# section number (``§ 10.99``, ``Sec. 1-4.``, ``Section 90.01``). Stripping it
# alone was measured at +13.5pp corpus-wide recall — the highest-EV quick win.
_SECTION_LABEL = re.compile(r"^(?:§+|[Ss]ec(?:tion|\.)?)\s*")
# Absolute dotted (``1.05.010``) — the authoritative skeleton when present.
_DOTTED_ADDRESS = re.compile(r"^([0-9]+(?:\.[0-9]+)+)\b")
# Absolute dash-dotted (``1-2-1``, ``38-1014``) — an equally-authoritative
# convention (Sec. 1-4. / 1-2-1:) used by a large share of the corpus. A bare
# trailing dash (``1-``) does not count; require >= 1 inner ``-NUMBER`` group.
_DASH_ADDRESS = re.compile(r"^([0-9]+(?:-[0-9]+)+)\b")
# Word containers (``Article 2``, ``Chapter 1``, ``Title 5``, ``Part 3``,
# ``Division 4``) — both arabic and roman-numeral values. These PUSH a typed
# container segment onto the path stack; the value is normalised to its source
# token (``article:II``, ``chapter:1``).
_WORD_CONTAINER = re.compile(
    r"^(article|chapter|title|part|division|subchapter|subpart|subdivision)\s+"
    r"([0-9]+[A-Za-z]?|[IVXLCDM]+|[A-Za-z])\b",
    re.IGNORECASE,
)
_CONTAINER_KIND = {
    "article": "article",
    "chapter": "chapter",
    "title": "title",
    "part": "part",
    "division": "division",
    "subchapter": "subchapter",
    "subpart": "subpart",
    "subdivision": "subdivision",
}
# Relative ordinal / letter markers (``(A)``, ``(a)``, ``(1)``, ``1.``, ``A.``).
# These APPEND under the current parent as a positionally-unique item segment.
# Legitimate only because LOCUS is a SNAPSHOT (no renumbering), so positional
# ids are stable — unlike amended law. Captures the inner token.
_PAREN_ITEM = re.compile(r"^\(([0-9]+|[A-Za-z]{1,3})\)")
_DOT_ITEM = re.compile(r"^([0-9]+|[A-Za-z])\.\s")

# The address segment names by depth (title.chapter.section.subsection...). A
# dotted number deeper than this many segments reuses ``level_<n>`` so deep
# numbering (San Jose ``N.N.N.N.N``) is still owned, never dropped.
_SEGMENT_KINDS: tuple[str, ...] = ("title", "chapter", "section", "subsection", "paragraph")


def _segment_kind(depth: int) -> str:
    """The structural kind of the ``depth``-th dotted segment (0-based)."""
    if depth < len(_SEGMENT_KINDS):
        return _SEGMENT_KINDS[depth]
    return f"level_{depth}"


# Induction-method tags (recall-visibility, NOT hidden). ``exact_dotted`` is the
# soundest (an authoritative dotted/dashed number); ``word_container`` /
# ``sequential_stack`` are heuristic (positional, snapshot-only); a row that
# yields none stays a typed ``locus_header_unparsed`` residual.
METHOD_EXACT_DOTTED = "exact_dotted"
METHOD_WORD_CONTAINER = "word_container"
METHOD_SEQUENTIAL_STACK = "sequential_stack"
METHOD_HEURISTIC = "heuristic"
INDUCTION_METHODS: tuple[str, ...] = (
    METHOD_EXACT_DOTTED,
    METHOD_WORD_CONTAINER,
    METHOD_SEQUENTIAL_STACK,
    METHOD_HEURISTIC,
)


# --------------------------------------------------------------------------- #
# Address induction                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class InducedAddress:
    """A header successfully induced to a multi-segment dotted address path.

    ``segments`` is the list of ``(structural_kind, value)`` pairs from title
    down to the leaf; ``address_path`` is the canonical ``title:1/chapter:05/
    section:010`` rendering used as the ``struct_node_id`` address input.
    ``method`` records HOW the address was induced (recall-visibility — heuristic
    induction is tagged, never hidden as if it were an authoritative number).
    """

    dotted: str
    segments: tuple[tuple[str, str], ...]
    method: str = METHOD_EXACT_DOTTED

    @property
    def address_path(self) -> str:
        return "/".join(f"{kind}:{value}" for kind, value in self.segments)

    @property
    def leaf_kind(self) -> str:
        return self.segments[-1][0]


def _strip_header(header: str) -> str:
    """Strip the markdown ``#`` prefix and surrounding whitespace from a header."""
    return _MD_HEADING.sub("", header).strip()


def _match_absolute(text: str) -> tuple[str, tuple[tuple[str, str], ...]] | None:
    """Match an absolute dotted / dashed number at the start of ``text``.

    Tries (1) a bare dotted/dashed number, then (2) the same after stripping a
    leading ``§`` / ``Sec.`` / ``Section`` label (the +13.5pp quick win). Returns
    ``(dotted_string, segments)`` or ``None``. Dash separators are normalised to
    dots for the segment values so ``1-2-1`` and ``1.2.1`` share an address shape.
    """
    for candidate in (text, _SECTION_LABEL.sub("", text, count=1)):
        m = _DOTTED_ADDRESS.match(candidate)
        if m is not None:
            dotted = m.group(1)
            parts = dotted.split(".")
            return dotted, tuple((_segment_kind(i), p) for i, p in enumerate(parts))
        m = _DASH_ADDRESS.match(candidate)
        if m is not None:
            dotted = m.group(1)
            parts = dotted.split("-")
            return dotted, tuple((_segment_kind(i), p) for i, p in enumerate(parts))
    return None


def induce_address(header: str | None) -> InducedAddress | None:
    """Induce an ABSOLUTE address from a LOCUS ``header`` (stateless primitive).

    This is the soundest, context-free induction: a multi-segment dotted
    (``1.05.010``) or dash-dotted (``1-2-1``, ``Sec. 1-4.``) number, optionally
    behind a ``§`` / ``Sec.`` / ``Section`` label. Returns ``None`` (→ a typed
    residual at the call site, never a silent drop) when no absolute number is
    present — a chapter/article TITLE heading, a bare subsection marker, or a
    null header. The contract is fail-loud: an un-inducible header is owned as a
    typed residual, never coerced into a phantom address.

    The DOCUMENT-ORDER fold (:class:`AddressInducer`) is the higher-recall path —
    it additionally resolves word containers (``Article II``) and relative
    ordinals (``(a)``, ``1.``) against a running path stack. This stateless
    primitive is retained for the absolute case and back-compat.
    """
    if not header:
        return None
    matched = _match_absolute(_strip_header(header))
    if matched is None:
        return None
    dotted, segments = matched
    return InducedAddress(dotted=dotted, segments=segments, method=METHOD_EXACT_DOTTED)


def _roman_or_token(value: str) -> str:
    """Normalise a container value token (kept as-is; lower-cased only for words)."""
    return value


@dataclass(slots=True)
class _StackFrame:
    """One level on the induction path stack: a structural segment + its depth-kind."""

    kind: str  # structural kind (title/chapter/article/section/item/...)
    value: str  # the segment value (``1``, ``05``, ``II``, ``a``)
    absolute: bool  # True if set by an absolute dotted/dashed number


class AddressInducer:
    """Stateful, document-ordered address induction (the max-recall fold).

    Rows of a work arrive in DOCUMENT ORDER (verified corpus-wide). The inducer
    maintains a path stack and, per row, applies the highest-priority NUMBER cue:

    * **absolute dotted/dashed** (``1.05.010``, ``§ 10.99``, ``1-2-1``) → RESET
      the stack to that absolute path (``exact_dotted``). The authoritative case.
    * **word container** (``Article II``, ``Chapter 1``) → push/replace a typed
      container frame at its container depth (``word_container``). A container has
      a canonical ordering (title > chapter > article > part > division), so a
      new container of an equal-or-shallower kind pops back to it first.
    * **relative ordinal/letter** (``(a)``, ``(1)``, ``1.``, ``A.``) → APPEND an
      ``item:<token>`` segment under the current parent (``sequential_stack``).
      Positionally unique — legitimate ONLY because LOCUS is a non-renumbered
      snapshot.

    A row whose header yields no cue AND has no established parent is genuinely
    hopeless → ``None`` (the caller writes a typed ``locus_header_unparsed``
    residual). Heuristic induction is always tagged via ``InducedAddress.method``
    so recall-max never masquerades as authoritative parsing.
    """

    # Container nesting rank — a smaller rank is structurally shallower. A new
    # container pops the stack back to (and replaces) frames of equal-or-deeper
    # rank, so ``Chapter 2`` after ``Article 5`` under ``Chapter 1`` reopens at
    # the chapter level rather than nesting under the article.
    _CONTAINER_RANK = {
        "title": 0,
        "subtitle": 1,
        "chapter": 2,
        "subchapter": 3,
        "article": 4,
        "part": 5,
        "subpart": 6,
        "division": 7,
        "subdivision": 8,
    }

    def __init__(self) -> None:
        self._stack: list[_StackFrame] = []
        self._item_counter = 0

    def _path_segments(self) -> tuple[tuple[str, str], ...]:
        return tuple((f.kind, f.value) for f in self._stack)

    def induce(self, header: str | None) -> InducedAddress | None:
        if not header:
            return None
        text = _strip_header(header)
        if not text:
            return None

        # (1) absolute dotted/dashed (optionally behind §/Sec.) — RESET.
        matched = _match_absolute(text)
        if matched is not None:
            dotted, segments = matched
            self._stack = [
                _StackFrame(kind=k, value=v, absolute=True) for k, v in segments
            ]
            return InducedAddress(dotted=dotted, segments=segments, method=METHOD_EXACT_DOTTED)

        # (2) word container (Article II / Chapter 1 / Title 5 / Part 3 ...).
        m = _WORD_CONTAINER.match(text)
        if m is not None:
            word = m.group(1).lower()
            kind = _CONTAINER_KIND.get(word, word)
            value = _roman_or_token(m.group(2))
            rank = self._CONTAINER_RANK.get(kind, 99)
            # Pop frames of equal-or-deeper container rank (and any item frames)
            # so a sibling/ancestor container reopens at the right level.
            while self._stack:
                top = self._stack[-1]
                top_rank = self._CONTAINER_RANK.get(top.kind, 99 if top.kind != "item" else 100)
                if top.kind == "item" or top_rank >= rank:
                    self._stack.pop()
                else:
                    break
            self._stack.append(_StackFrame(kind=kind, value=value, absolute=False))
            segments = self._path_segments()
            return InducedAddress(
                dotted="/".join(f"{k}:{v}" for k, v in segments),
                segments=segments,
                method=METHOD_WORD_CONTAINER,
            )

        # (3) relative ordinal/letter — APPEND under the current parent.
        token = self._relative_token(text)
        if token is not None:
            if not self._stack:
                # No parent established — a relative marker with nothing to hang
                # it under is genuinely ambiguous; residualize.
                return None
            # Pop a prior item frame at the same nesting (a sibling list item)
            # only if the previous frame is also an item — keeps siblings flat
            # rather than ever-deepening. We keep nesting shallow: one item level.
            if self._stack and self._stack[-1].kind == "item":
                self._stack.pop()
            self._stack.append(_StackFrame(kind="item", value=token, absolute=False))
            segments = self._path_segments()
            return InducedAddress(
                dotted="/".join(f"{k}:{v}" for k, v in segments),
                segments=segments,
                method=METHOD_SEQUENTIAL_STACK,
            )

        return None

    @staticmethod
    def _relative_token(text: str) -> str | None:
        m = _PAREN_ITEM.match(text)
        if m is not None:
            return m.group(1)
        m = _DOT_ITEM.match(text)
        if m is not None:
            return m.group(1)
        return None


# --------------------------------------------------------------------------- #
# Row model + reader                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LocusRow:
    """One LOCUS parquet row, narrowed to the columns this adapter consumes."""

    row_index: int
    header: str | None
    content: str | None
    is_substantive: bool
    function: str | None
    topic: str | None
    scores: dict[str, float | None]


@dataclass(frozen=True, slots=True)
class WorkKey:
    """A work = one municipality = (state, city|county, jurisdiction_type)."""

    state: str
    city: str | None
    county: str | None
    jurisdiction_type: str

    @property
    def locality(self) -> str:
        return self.city if self.city else (self.county or "")

    @property
    def work_id(self) -> str:
        # us-local:<jtype>:<state>/<locality> — stable, jurisdiction-neutral.
        return f"{JURISDICTION}:{self.jurisdiction_type}:{self.state}/{self.locality}"

    @property
    def title(self) -> str:
        loc = self.locality.replace("_", " ").title()
        return f"{loc}, {self.state.upper()} ({self.jurisdiction_type})"


def read_work_rows(parquet_glob: str, key: WorkKey) -> list[LocusRow]:
    """Read all rows of one work from the LOCUS parquet, in document order.

    Uses duckdb (an extra dependency installed via ``uv run --with duckdb`` in
    the acceptance harness; imported lazily so the module imports without it).
    Document order is the parquet row order within the work — the address tree
    is induced from the header numbering, not from row order, but order is kept
    stable for deterministic residual reporting.
    """
    try:
        import duckdb  # lazy — only the snapshot path needs it
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise ModuleNotFoundError(
            "the LOCUS snapshot adapter needs duckdb to read parquet; install the "
            "'analytics' extra (uv pip install -e '.[analytics]') or run with "
            "'uv run --with duckdb'"
        ) from exc

    where = [f"state = '{_sql(key.state)}'", f"source_jurisdiction_type = '{_sql(key.jurisdiction_type)}'"]
    if key.city is not None:
        where.append(f"city = '{_sql(key.city)}'")
    else:
        where.append("city IS NULL")
    if key.county is not None:
        where.append(f"county = '{_sql(key.county)}'")
    else:
        where.append("county IS NULL")
    cols = (
        "header, content, is_substantive, function, topic, "
        + ", ".join(_SCORE_COLUMNS)
    )
    sql = (
        f"SELECT {cols} FROM read_parquet('{parquet_glob}') "
        f"WHERE {' AND '.join(where)}"
    )
    con = duckdb.connect()
    try:
        records = con.execute(sql).fetchall()
    finally:
        con.close()
    out: list[LocusRow] = []
    for i, rec in enumerate(records):
        header, content, is_subst, function, topic = rec[0], rec[1], rec[2], rec[3], rec[4]
        scores = {col: rec[5 + j] for j, col in enumerate(_SCORE_COLUMNS)}
        out.append(
            LocusRow(
                row_index=i,
                header=header,
                content=content,
                is_substantive=bool(is_subst),
                function=function,
                topic=topic,
                scores=scores,
            )
        )
    return out


def _sql(value: str) -> str:
    """Escape a single-quoted SQL literal (defensive — keys come from the corpus)."""
    return value.replace("'", "''")


# --------------------------------------------------------------------------- #
# Overlay body                                                                  #
# --------------------------------------------------------------------------- #


def _overlay_body(
    content_leaf_hash: str,
    function: str | None,
    topic: str | None,
    scores: dict[str, float | None],
) -> dict[str, JsonValue]:
    """``lawvm.overlay.v1`` — analytical enrichers anchored to the content leaf.

    Determinism firewall (design §22.2/§22.3): ``producer.determinism =
    external_generated`` and ``authority.surface_only = true`` /
    ``replay_authorized = false`` — this overlay can NEVER mutate legal state.
    Floats are forbidden in the canonical-JSON hashed body (§1.4), so each score
    is carried as its exact string rendering (the analytical value is preserved,
    the hash stays deterministic).
    """
    payload: dict[str, JsonValue] = {
        "function": nfc(function) if function else None,
        "topic": nfc(topic) if topic else None,
        "scores": {col: (None if scores.get(col) is None else repr(scores[col])) for col in _SCORE_COLUMNS},
    }
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_OVERLAY,
        "overlay_kind": "lawvm.overlay.locus_analytical.v0",
        "anchor": {"anchor_kind": "content_leaf", "anchor_id": content_leaf_hash},
        "producer": {
            "producer_id": "locus.analytical.v0",
            "producer_version": "v0",
            "determinism": "external_generated",
        },
        "authority": {
            "surface_only": True,
            "replay_authorized": False,
            "projection_not_source": True,
        },
        "locus_status": "informational",
        "payload": payload,
    }
    body["overlay_id"] = leaf_hash(_DOMAIN_OVERLAY, body)
    return body


# --------------------------------------------------------------------------- #
# Content leaf (text-only identity, mirrors the exporter)                      #
# --------------------------------------------------------------------------- #

SCHEMA_CONTENT_LEAF = "lawvm.content_leaf.v1"
_DOMAIN_CONTENT_LEAF = "content_leaf"


def _content_leaf_body(text: str) -> tuple[str, dict[str, JsonValue]]:
    """PURE text-only content leaf (NFC + ``sha256:`` via substrate primitives).

    Identical identity discipline to ``exporter._content_leaf_body`` — the body
    is ``{schema, text, content_leaf_hash}`` and NOTHING per-work, so identical
    leaf text in two municipalities yields a byte-identical object that
    deduplicates at the shared-store level (design §22.1 anchor ladder). The
    per-occurrence ``source_locators`` ride on the ``node_version`` instead.
    Re-built here (rather than imported) only because the exporter's variant
    takes an ``IRNode``; the hashed body shape is the same ``lawvm.content_leaf.v1``.
    """
    normalized = nfc(text)
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_CONTENT_LEAF,
        "text": normalized,
    }
    content_leaf_hash = leaf_hash(_DOMAIN_CONTENT_LEAF, body)
    body["content_leaf_hash"] = content_leaf_hash
    return content_leaf_hash, body


SCHEMA_NODE_VERSION = "lawvm.node_version.v1"
_DOMAIN_NODE_VERSION = "node_version"


def _node_version_body(
    struct_node_id: str,
    content_leaf_hash: str,
    produced_by: str,
    source_locators: list[JsonValue],
) -> tuple[str, dict[str, JsonValue]]:
    """``lawvm.node_version.v1`` over a single open-ended snapshot interval.

    ``source_locators`` are the per-work-per-occurrence source spans; they live
    on the node_version (not the shared content leaf) so the leaf stays pure
    text and deduplicates across municipalities (design §22.1). They are a
    visible body member but NOT part of the ``node_version_id`` identity tuple.
    """
    identity: dict[str, JsonValue] = {
        "schema": SCHEMA_NODE_VERSION,
        "struct_node_id": struct_node_id,
        "produced_by_transition_id": produced_by,
        "content_leaf_hash": content_leaf_hash,
        "effective_interval": [SNAPSHOT_DATE, None],
        "branch_id": _BRANCH_ID,
        "rail": _RAIL_PERMANENT,
    }
    node_version_id = leaf_hash(_DOMAIN_NODE_VERSION, identity)
    body = dict(identity)
    body["node_version_id"] = node_version_id
    body["source_locators"] = list(source_locators)
    return node_version_id, body


# --------------------------------------------------------------------------- #
# Result summary                                                                #
# --------------------------------------------------------------------------- #


@dataclass
class SnapshotResult:
    """Summary of one emitted snapshot pack (the CLI prints this)."""

    work_id: str
    out_dir: str
    pack_id: str
    n_rows: int
    n_addressable_leaves: int
    n_content_leaves: int
    n_address_nodes: int
    n_selection_rows: int
    n_overlay_rows: int
    n_residuals: int
    header_parse_residuals: int
    residual_kinds: tuple[str, ...]
    method_counts: dict[str, int]


# --------------------------------------------------------------------------- #
# The snapshot producer                                                         #
# --------------------------------------------------------------------------- #

# Filled layers: base/state/proof carry rows; trace is EMPTY for a snapshot (no
# transitions), overlay carries the analytical enrichers. The reserved dirs
# mirror the exporter so a whole-family omission is committed to.
_FILLED_LAYERS: tuple[tuple[str, str, str], ...] = (
    ("base", "base/base.jsonl", "SetRoot"),
    ("state", "state/state.jsonl", "SetRoot"),
    ("trace", "trace/trace.jsonl", "SeqRoot"),
    ("proof", "proof/proof.jsonl", "SetRoot"),
    ("overlay", "overlay/overlay.jsonl", "SetRoot"),
)
_RESERVED_DIRS: tuple[str, ...] = ("surface", "edges", "branch", "projection", "dict")


def export_snapshot_pack(
    parquet_glob: str,
    key: WorkKey,
    out_dir: str | Path,
    *,
    rows: Sequence[LocusRow] | None = None,
    emit_overlay: bool = True,
    quiet: bool = False,
) -> SnapshotResult:
    """Read one LOCUS work, induce its address tree, emit a sparse snapshot pack.

    ``rows`` may be supplied directly (tests / synthetic works); otherwise they
    are read from ``parquet_glob`` for ``key`` via duckdb. The pack is the SAME
    object-model → manifest → checker shape the FI exporter emits — one
    ``InitialStateEvent`` (genesis ``observed_codification_snapshot``), induced
    address nodes, deduped content leaves, one selection row per leaf over
    ``[SNAPSHOT_DATE, +inf)``, the selection universe, and (optionally) the
    analytical overlay. No transitions, no replay.
    """
    if rows is None:
        rows = read_work_rows(parquet_glob, key)

    work_id = key.work_id
    corpus_version = f"{JURISDICTION}:corpus:{_dt.date.today().isoformat()}"
    out = Path(out_dir)
    if out.exists():
        import shutil

        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    writers: dict[str, _LayerWriter] = {}
    for kind, fname, root_fn in _FILLED_LAYERS:
        writers[kind] = _LayerWriter(out / fname, root_fn)
    for reserved in _RESERVED_DIRS:
        (out / reserved).mkdir(parents=True, exist_ok=True)

    base_w = writers["base"]
    state_w = writers["state"]
    proof_w = writers["proof"]
    overlay_w = writers["overlay"]

    # -- source plane: one record + one manifestation per work --------------- #
    source_record = SourceRecord(
        jurisdiction=JURISDICTION,
        keeper="locus",
        logical_kind=LogicalKind.MUNICIPAL_CODE_PDF,
        logical_key=work_id,
        work_id_hint=work_id,
    )
    base_w.write(source_record.to_canonical_dict())
    manifestation = SourceManifestation(
        source_record_id=source_record.source_record_id,
        # Digest of the observed snapshot text set (a stable per-work anchor that
        # does not churn on re-fetch — the immutable creation-event anchor §8.2).
        raw_witness_hash=_witness_hash(rows),
        media_type="text/markdown",
        fetched_at=SNAPSHOT_DATE,
        locator=Locator(scheme="farchive", value=f"farchive:locus:{work_id}", byte_count=None),
        availability=Availability.DIGEST_ONLY,
    )
    base_w.write(manifestation.to_canonical_dict())
    source_ref = manifestation.locator.value

    # -- work + selection profiles + total scope predicate ------------------- #
    base_w.write(_work_body(work_id, key.title, JURISDICTION, corpus_version))

    total_scope = ScopePredicate(dimensions={}, scope_status="total")
    scope_predicate_id = total_scope.scope_predicate_id
    scope_predicate_hashes = [state_w.write(total_scope.to_canonical_dict())]

    selection_profile_hashes: list[str] = []
    for prof in v0_profiles():
        selection_profile_hashes.append(state_w.write(prof.to_canonical_dict()))

    # -- genesis: ONE observed_codification_snapshot ------------------------- #
    genesis = InitialStateEvent(
        work_id=work_id,
        genesis_kind=GenesisKind.OBSERVED_CODIFICATION_SNAPSHOT,
        effective_date=SNAPSHOT_DATE,
        prior_history_status=PriorHistoryStatus.UNMODELED,
        source_refs=(source_ref,),
        # Snapshot genesis: the immutable manifestation id is the creation anchor.
        creation_event_id=manifestation.manifestation_id,
    )
    base_w.write(genesis.to_canonical_dict())

    # -- per-row induction + emission ---------------------------------------- #
    content_leaf_hashes: list[str] = []
    node_version_hashes: list[str] = []
    applicability_fact_hashes: list[str] = []
    candidate_set_hashes: list[str] = []
    selection_row_hashes: list[str] = []
    overlay_hashes: list[str] = []

    emitted_content_leaves: dict[str, str] = {}
    address_nodes_seen: set[str] = set()
    address_kind: dict[str, str] = {}
    expected_selection_keys: dict[str, str] = {}
    all_addresses: set[str] = set()
    claimed_leaf_addresses: set[str] = set()
    # Container (title/chapter) addresses — structural grouping nodes that carry
    # no text-state of their own. They are addressable nodes (so totality counts
    # them), so each gets an OWNING ``absent`` selection row post-loop (a typed
    # non-selection reason: a grouping node, never a silent unowned gap).
    container_addresses: set[str] = set()

    n_addressable = 0
    header_parse_residuals = 0
    residual_kinds: set[str] = set()
    method_counts: dict[str, int] = {m: 0 for m in INDUCTION_METHODS}
    inducer = AddressInducer()

    def _ensure_address_node(address_path: str, structural_kind: str) -> str:
        if address_path not in address_nodes_seen:
            address_nodes_seen.add(address_path)
            address_kind[address_path] = structural_kind
            base_w.write(_address_node_body(work_id, address_path, structural_kind))
        return _struct_node_id(work_id, address_path, address_kind[address_path])

    def _ensure_content_leaf(text: str) -> str:
        clh, body = _content_leaf_body(text)
        existing = emitted_content_leaves.get(clh)
        if existing is not None:
            return clh
        base_w.write(body)
        emitted_content_leaves[clh] = clh
        content_leaf_hashes.append(clh)
        return clh

    for row in rows:
        induced = inducer.induce(row.header)
        if induced is None:
            # Un-inducible header → TYPED residual (never a silent drop). The
            # offending header text is embedded (self-evidencing diagnostics).
            header_parse_residuals += 1
            kind = "locus_header_unparsed"
            residual_kinds.add(kind)
            detail = (
                f"header {row.header!r} carries no multi-segment dotted address "
                f"(chapter/article title, subsection marker, or §-style number); "
                f"owned as a typed residual, not coerced into a phantom address"
            )
            proof_w.write(_residual_body(kind, False, detail, f"row:{row.row_index}"))
            continue

        # Ancestor structural nodes (title, chapter, ...) up to the leaf.
        for depth in range(1, len(induced.segments)):
            ancestor_path = "/".join(
                f"{k}:{v}" for k, v in induced.segments[:depth]
            )
            _ensure_address_node(ancestor_path, induced.segments[depth - 1][0])
            all_addresses.add(ancestor_path)
            container_addresses.add(ancestor_path)

        leaf_path = induced.address_path
        if leaf_path in claimed_leaf_addresses:
            # Two rows induced to the same leaf address — a duplicate-address
            # collision (design §16 ``duplicate_address_label``). Own it as a
            # typed residual; the first occurrence keeps the address.
            kind = "locus_duplicate_address"
            residual_kinds.add(kind)
            detail = (
                f"header {row.header!r} induces address {leaf_path!r} already "
                f"claimed by an earlier row in this work (duplicate section label)"
            )
            proof_w.write(_residual_body(kind, False, detail, f"row:{row.row_index}"))
            continue
        claimed_leaf_addresses.add(leaf_path)

        struct_id = _ensure_address_node(leaf_path, induced.leaf_kind)
        all_addresses.add(leaf_path)
        n_addressable += 1
        method_counts[induced.method] = method_counts.get(induced.method, 0) + 1

        text = row.content if row.content is not None else ""
        clh = _ensure_content_leaf(text)

        produced_by = f"genesis:{leaf_path}"
        nv_id, nv_body = _node_version_body(struct_id, clh, produced_by, [source_ref])
        node_version_hashes.append(state_w.write(nv_body))

        fact = ApplicabilityFact(
            work_id=work_id,
            address_id=struct_id,
            node_version_id=nv_id,
            content_leaf_hash=clh,
            branch_id=_BRANCH_ID,
            effect_interval=(SNAPSHOT_DATE, None),
            enactment_interval=(SNAPSHOT_DATE, None),
            account_interval=(corpus_version, None),
            rail=_RAIL_PERMANENT,
            scope_predicate_id=scope_predicate_id,
            precedence_class="same_rail_latest",
            temporal_basis=TemporalBasis(kind="source_checkpoint"),
            produced_by_transition_id=produced_by,
        )
        applicability_fact_hashes.append(state_w.write(fact.to_canonical_dict()))

        cand = SelectionCandidate(
            node_version_id=nv_id,
            rail=_RAIL_PERMANENT,
            effect_interval=(SNAPSHOT_DATE, None),
            scope_predicate_id=scope_predicate_id,
            eligible=True,
        )
        cset = SelectionCandidateSet(
            selection_key=f"{struct_id}:{SNAPSHOT_DATE}",
            candidates=(cand,),
            complete=True,
        )
        cs_object_hash = state_w.write(cset.to_canonical_dict())
        candidate_set_hashes.append(cs_object_hash)

        selrow = SelectionRow(
            work_id=work_id,
            query_profile_id=_PROFILE_ID,
            branch_id=_BRANCH_ID,
            address_id=struct_id,
            scope_query_id=scope_predicate_id,
            effect_interval=(SNAPSHOT_DATE, None),
            account_interval=(corpus_version, None),
            source_policy_id="archival_exact",
            selection_status="selected",
            candidate_set_hash=cs_object_hash,
            selected_node_version_id=nv_id,
            decision_basis=DecisionBasis(
                selection_rule_id=_PROFILE_ID,
                applicability_fact_refs=(fact.fact_id,),
            ),
        )
        selection_key = selrow.selection_key
        row_body = selrow.to_canonical_dict()
        row_body["selection_key"] = selection_key
        row_object_hash = state_w.write(row_body)
        selection_row_hashes.append(row_object_hash)
        expected_selection_keys[selection_key] = row_object_hash

        # Analytical overlay anchored to the content leaf (determinism firewall).
        if emit_overlay and (
            row.function or row.topic or any(v is not None for v in row.scores.values())
        ):
            overlay_hashes.append(
                overlay_w.write(_overlay_body(clh, row.function, row.topic, row.scores))
            )

    # -- container ownership: an ``absent`` selection row per grouping node --- #
    # A title/chapter container is an addressable node with no text-state of its
    # own. Totality requires every addressable node owned; an ``absent`` row is
    # the typed non-selection reason (a grouping node, not a silent gap). A path
    # that is ALSO a claimed leaf already has a ``selected`` row — skip it.
    n_container_rows = 0
    for cpath in sorted(container_addresses - claimed_leaf_addresses):
        struct_id = _struct_node_id(work_id, cpath, address_kind[cpath])
        absent_row = SelectionRow(
            work_id=work_id,
            query_profile_id=_PROFILE_ID,
            branch_id=_BRANCH_ID,
            address_id=struct_id,
            scope_query_id=scope_predicate_id,
            effect_interval=(SNAPSHOT_DATE, None),
            account_interval=(corpus_version, None),
            source_policy_id="archival_exact",
            selection_status="absent",
        )
        absent_key = absent_row.selection_key
        absent_body = absent_row.to_canonical_dict()
        absent_body["selection_key"] = absent_key
        absent_hash = state_w.write(absent_body)
        selection_row_hashes.append(absent_hash)
        expected_selection_keys[absent_key] = absent_hash
        n_container_rows += 1

    # -- coverage (proof layer) ---------------------------------------------- #
    # Every residual written so far is a typed ``lawvm.residual.v1`` row (the two
    # coverage rows below are the only non-residual proof rows). The count is the
    # proof writer's current row_count — all residuals, before the coverage rows.
    n_residuals = proof_w.row_count
    proof_w.write(
        _coverage_body("owned", len(selection_row_hashes), "selected snapshot selection rows")
    )
    proof_w.write(
        _coverage_body(
            "residual",
            n_residuals,
            "typed header-parse / duplicate-address residuals",
        )
    )
    # Induction-method breakdown (recall-visibility — heuristic induction is
    # surfaced as its own coverage class, never hidden inside the owned count).
    # ``exact_dotted`` is the soundest; ``word_container`` / ``sequential_stack``
    # are positional/snapshot-only heuristics. The auditor reads these to know
    # exactly how much of the owned address tree rests on each method.
    # Carried under the ``benign`` coverage class (informational accounting, not
    # a fifth class) with the method name in the detail — the closed 4-class
    # coverage exhaustiveness check (owned/benign/residual/violation) stays intact.
    for method in INDUCTION_METHODS:
        cnt = method_counts.get(method, 0)
        if cnt:
            proof_w.write(
                _coverage_body(
                    "benign",
                    cnt,
                    f"induction_method:{method} — addressable leaves induced via {method}"
                    + (
                        " (heuristic, positional — snapshot-only)"
                        if method != METHOD_EXACT_DOTTED
                        else " (authoritative dotted/dashed number)"
                    ),
                )
            )

    # -- selection universe (omission keystone) ------------------------------ #
    universe = SelectionUniverse(
        work_id=work_id,
        query_profile_ids=(_PROFILE_ID,),
        branch_ids=(_BRANCH_ID,),
        expected_selection_keys=expected_selection_keys,
        address_root=set_root(
            "address_universe", [leaf_hash("addr", a) for a in sorted(all_addresses)]
        ),
        effect_boundary_root=set_root("effect_boundary", [leaf_hash("effect", SNAPSHOT_DATE)]),
        account_boundary_root=set_root("account_boundary", [leaf_hash("account", corpus_version)]),
        scope_query_root=set_root("scope_query", [scope_predicate_id]),
    )
    selection_universe_hashes = [state_w.write(universe.to_canonical_dict())]

    for w in writers.values():
        w.close()

    # -- roots --------------------------------------------------------------- #
    state_roots = build_state_selection_roots(
        selection_profile_object_hashes=selection_profile_hashes,
        selection_universe_object_hashes=selection_universe_hashes,
        scope_predicate_object_hashes=scope_predicate_hashes,
        applicability_fact_object_hashes=applicability_fact_hashes,
        candidate_set_object_hashes=candidate_set_hashes,
        selection_row_object_hashes=selection_row_hashes,
    )
    content_leaf_root = set_root("content_leaf", content_leaf_hashes)
    node_version_root = set_root("node_version", node_version_hashes)
    projection_root = set_root("projection", [])
    index_roots = build_selection_index_roots(
        content_leaf_root=content_leaf_root,
        node_version_root=node_version_root,
        state_selection_root=state_roots.state_selection_root,
        projection_root=projection_root,
    )
    # The trace layer is empty for a snapshot (no transitions); its
    # materialization root is a SeqRoot over zero checkpoint rows.
    materialization_root = seq_root("materialization", writers["trace"].hashes)

    certificate_root, cert_body = _build_certificate(
        work_id=work_id,
        materialization_root=materialization_root,
        selection_index_root=index_roots.selection_index_root,
        n_residuals=n_residuals,
    )
    cert_dir = out / "cert"
    cert_dir.mkdir(parents=True, exist_ok=True)
    (cert_dir / "certificate.json").write_text(
        json.dumps(wrap_row(cert_body), ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    source_bundle_root = leaf_hash("source_bundle", {"source_refs": [source_ref]})
    roots = {
        "materialization_root": materialization_root,
        "selection_index_root": index_roots.selection_index_root,
        "certificate_root": certificate_root,
        "source_bundle_root": source_bundle_root,
    }

    layers = _build_layer_descriptors_snapshot(writers, materialization_root, certificate_root)

    schemas = {
        "work": "lawvm.work.v1",
        "address_node": "lawvm.address_node.v1",
        "content_leaf": SCHEMA_CONTENT_LEAF,
        "node_version": SCHEMA_NODE_VERSION,
        "selection_row": "lawvm.selection_row.v1",
        "applicability_fact": "lawvm.applicability_fact.v1",
        "initial_state_event": "lawvm.initial_state_event.v1",
        "overlay": SCHEMA_OVERLAY,
    }
    provenance = PackProvenance(
        lawvm_git_commit=_git_commit(),
        engine_version="lawvm.snapshot.locus",
        source_policy_id="archival_exact",
        checkable_source_bundle_policy="archival_exact",
        created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        dirty_tree=False,
    )
    manifest = PackManifest(
        pack_kind=PACK_KIND,
        work_ids=(work_id,),
        corpus_version=corpus_version,
        identity_encoding=IDENTITY_ENCODING,
        storage_codec=STORAGE_CODEC,
        dict_id="",
        profiles=(CANON_PROFILE,),
        selection_profiles=(_PROFILE_ID,),
        schemas=schemas,
        layers=layers,
        roots=roots,
        required_layers_for_browse=("base", "state", "cert"),
        required_layers_for_audit=("base", "state", "trace", "proof", "cert"),
        optional_layers=("surface", "edges", "branch", "overlay", "projection", "dict"),
        provenance=provenance,
    )
    (out / "manifest.json").write_text(
        json.dumps(
            wrap_row(manifest.to_canonical_dict()),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    return SnapshotResult(
        work_id=work_id,
        out_dir=str(out),
        pack_id=manifest.pack_id,
        n_rows=len(rows),
        n_addressable_leaves=n_addressable,
        n_content_leaves=len(content_leaf_hashes),
        n_address_nodes=len(address_nodes_seen),
        n_selection_rows=len(selection_row_hashes),
        n_overlay_rows=len(overlay_hashes),
        n_residuals=n_residuals,
        header_parse_residuals=header_parse_residuals,
        residual_kinds=tuple(sorted(residual_kinds)),
        method_counts=dict(method_counts),
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                        #
# --------------------------------------------------------------------------- #


def _witness_hash(rows: Sequence[LocusRow]) -> str:
    """A stable per-work witness digest over the observed (header, content) set.

    Anchors the manifestation id; deterministic over the snapshot bytes so it
    does not churn on re-export (it is the immutable creation-event anchor).
    """
    payload = [[r.header or "", r.content or ""] for r in rows]
    return leaf_hash("locus_witness", cast(JsonValue, payload))


SCHEMA_CERTIFICATE = "lawvm.certificate.v0"


def _build_certificate(
    *,
    work_id: str,
    materialization_root: str,
    selection_index_root: str,
    n_residuals: int,
) -> tuple[str, dict[str, JsonValue]]:
    subroots = [materialization_root, selection_index_root]
    certificate_root = set_root("certificate", subroots)
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_CERTIFICATE,
        "work_id": work_id,
        "materialization_root": materialization_root,
        "selection_index_root": selection_index_root,
        "certificate_root": certificate_root,
        "residual_count": n_residuals,
        "certification_status": "clean" if n_residuals == 0 else "qualified",
    }
    return certificate_root, body


def _build_layer_descriptors_snapshot(
    writers: dict[str, _LayerWriter],
    materialization_root: str,
    certificate_root: str,
) -> tuple[Any, ...]:
    """One PackLayer per filled layer (base/state/trace/proof/overlay).

    The overlay layer's row schema is the local enrichment schema; the checker
    treats overlay as an OPTIONAL layer, so an unknown overlay schema yields
    ``VALID_WITH_UNSUPPORTED_LAYERS`` rather than failing the pack.
    """
    from lawvm.substrate.manifest import PackLayer

    layer_domain = {
        "base": "base",
        "state": "state",
        "trace": "trace",
        "proof": "proof",
        "overlay": "overlay",
    }
    descriptors: list[PackLayer] = []
    for kind in ("base", "state", "trace", "proof", "overlay"):
        w = writers[kind]
        root = w.root(layer_domain[kind])
        descriptors.append(
            PackLayer(
                kind=kind,
                path=f"{kind}/{kind}.jsonl",
                row_schema=f"lawvm.layer.{kind}.v0",
                codec=STORAGE_CODEC,
                dict_id="",
                uncompressed_sha256=w.uncompressed_sha256(),
                storage_sha256=w.uncompressed_sha256(),
                root=root,
                root_fn=w.root_fn,
                row_count=w.row_count,
            )
        )
    return tuple(descriptors)


# --------------------------------------------------------------------------- #
# Snapshot pack reader (for check-pack on a snapshot pack)                      #
# --------------------------------------------------------------------------- #

# The snapshot pack emits source-plane objects (``source_record`` /
# ``source_manifestation``) into ``base/`` and an analytical ``overlay`` — none
# of which the FI exporter emits. The exporter's reader hardcodes its own
# ``_KNOWN_SCHEMAS`` (no source objects), so a snapshot pack read through it
# would wrongly flag ``UNSUPPORTED_SCHEMA`` on the source rows. This reader reuses
# the exporter's manifest reconstruction + the substrate ``Pack`` shape verbatim
# and supplies the EXTENDED known-schema set (the source-lineage cluster is part
# of the substrate vocabulary, just unused by the FI replay producer).
_SNAPSHOT_KNOWN_SCHEMAS = frozenset(
    {
        "lawvm.work.v1",
        "lawvm.address_node.v1",
        SCHEMA_CONTENT_LEAF,
        SCHEMA_NODE_VERSION,
        SCHEMA_CERTIFICATE,
        "lawvm.residual.v1",
        "lawvm.coverage_row.v1",
        "lawvm.selection_row.v1",
        "lawvm.applicability_fact.v1",
        "lawvm.selection_candidate_set.v1",
        "lawvm.scope_predicate.v1",
        "lawvm.selection_profile.v1",
        "lawvm.selection_universe.v1",
        "lawvm.initial_state_event.v1",
        # The source-lineage cluster (emitted into base/ by the snapshot producer).
        "lawvm.source_record.v1",
        "lawvm.source_manifestation.v1",
        # NOTE: lawvm.overlay.v1 is deliberately EXCLUDED — overlay is an optional
        # layer, so its unknown schema yields VALID_WITH_UNSUPPORTED_LAYERS (the
        # determinism firewall: the enricher is surfaced as not-core, never fails
        # the legal-state pack).
    }
)


def load_snapshot_pack_for_check(pack_dir: str | Path) -> Any:
    """Read a snapshot pack back into a checker :class:`Pack` (extended schemas).

    Identical to :func:`lawvm.substrate.exporter.load_pack_for_check` except for
    the known-schema set (which includes the source-lineage objects the snapshot
    producer emits into ``base/``). The manifest reconstruction, layer reading,
    and selection-universe map are reused from the exporter unchanged.
    """
    from lawvm.substrate.checker import Pack, PackLayerData
    from lawvm.substrate.exporter import _manifest_from_body
    from lawvm.substrate.roots import map_root

    pack_path = Path(pack_dir)
    manifest_row = json.loads((pack_path / "manifest.json").read_text(encoding="utf-8"))
    manifest_body = manifest_row["object"] if "object" in manifest_row else manifest_row
    manifest = _manifest_from_body(manifest_body)

    layers: dict[str, PackLayerData] = {}
    for layer in manifest.layers:
        kind = layer.kind
        rows_out: list[dict[str, JsonValue]] = []
        layer_file = pack_path / layer.path
        if layer_file.exists():
            with layer_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rows_out.append(json.loads(line))
        layers[kind] = PackLayerData(
            kind=kind,
            domain=kind,
            root_fn=layer.root_fn,
            root=layer.root,
            rows=tuple(rows_out),
        )

    selection_universe: dict[str, str] | None = None
    selection_universe_root: str | None = None
    state = layers.get("state")
    if state is not None:
        universe_keys: dict[str, str] = {}
        for row in state.rows:
            body = row.get("object")
            if not isinstance(body, dict):
                continue
            typed_body = cast("dict[str, Any]", body)
            if typed_body.get("schema") == "lawvm.selection_row.v1":
                key = typed_body.get("selection_key")
                if isinstance(key, str):
                    universe_keys[key] = str(row["object_hash"])
        if universe_keys:
            selection_universe = universe_keys
            selection_universe_root = map_root("selection_universe", universe_keys)

    return Pack(
        manifest=manifest,
        layers=layers,
        selection_universe=selection_universe,
        selection_universe_root=selection_universe_root,
        referenced_hashes={},
        known_schemas=_SNAPSHOT_KNOWN_SCHEMAS,
    )
