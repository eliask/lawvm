"""MinerU2.5-Pro structural table producer — a FIREWALLED, VERIFIED candidate witness.

MinerU (``opendatalab/MinerU2.5-Pro``, Apache-2.0) is a doc-parsing VLM with a
DEDICATED layout-detection stage + specialized table/formula recognition heads.
On the phase-A(3) residual — dense / nested / spanning statute appendix tables
(the ``appendix_only`` / ``xml_frame_only`` stratum where the geometric and
Docling lanes collapse) — it emits a near-exact HTML cell grid with correct
``rowspan`` / ``colspan`` topology and reading order. On ``kalavesi`` our
``struct_span`` lane returned 0 cells; MinerU rendered the same page at 215/216
cells with the nested header topology intact.

It is a CANDIDATE, never an oracle. MinerU makes rare single-glyph errors
(``INGARSKILAÅN`` → ``INGARSKILAÄN`` (Å→Ä), ``TARKEMMAT`` → ``TARKÉMMAT``), so a
MinerU cell graduates only when an INDEPENDENT witness corroborates it — see the
VERIFY GATE below. Trust it MOST for topology / structure, LEAST for rare glyphs.

CORRELATED-FAILURE CAVEAT (assessment §3b). MinerU2.5-Pro is Qwen2-VL-lineage and
our :8080 vision witness is Qwen3.6-35B — both Qwen VLMs, so at the vision-encoder
/ glyph-OCR level their priors are CORRELATED (they tend to share diacritic
mistakes). The independent leg for GLYPH adjudication is therefore the BORN-DIGITAL
TEXT LAYER (and any non-Qwen deterministic producer), NOT the Qwen vision witness.
The verify gate here uses exactly that text-layer leg.

──────────────────────────────────────────────────────────────────────────────
THE FIREWALL (the key engineering constraint)
──────────────────────────────────────────────────────────────────────────────
``mineru`` (PyPI 3.4.4) requires CPython ``<3.14``; this repo's venv is 3.14. So
MinerU is NEVER a repo dependency (it is deliberately absent from ``pyproject``).
It runs as an EXTERNAL PRODUCER in a SEPARATE ``uv venv --python 3.12`` (weights +
env live OUTSIDE the repo, gitignored) and is invoked as a SUBPROCESS. One-time
setup (documented, NOT committed)::

    uv venv --python 3.12 ~/.cache/lawvm/mineru_env
    VIRTUAL_ENV=~/.cache/lawvm/mineru_env uv pip install \
        "torch>=2.6" torchvision --index-url https://download.pytorch.org/whl/cpu
    VIRTUAL_ENV=~/.cache/lawvm/mineru_env uv pip install "mineru[vlm]"

Point the producer at that venv via ``LAWVM_MINERU_VENV`` (or the default
``~/.cache/lawvm/mineru_env``). The GPU may be occupied by the :8080 server, so
the producer runs CPU-only (``MINERU_DEVICE_MODE=cpu``, ~3 min/page — fine for an
offline proof batch; a GPU-when-free path is a follow-up). The subprocess is run
from a CLEAN cwd (a stray ``profile.py`` shadows stdlib ``profile`` and yields a
misleading "install transformers" error).

──────────────────────────────────────────────────────────────────────────────
DETERMINISM FIREWALL (AGENTS.md §1.3) + content-addressed store
──────────────────────────────────────────────────────────────────────────────
MinerU decoding is greedy (``top_k=1``) — empirically byte-identical on re-run —
but bf16 numerics can differ ACROSS device / dtype / transformers version. So the
raw MinerU output is persisted CONTENT-ADDRESSED by
``(artifact_digest, page, model_id, device, dtype, transformers_version)``
(:class:`MineruTableStore`, the same immutable-source / mutable-derived pattern as
:class:`~lawvm.ingest.recovered_text_store.RecoveredTextStore` and the derived-IR
store). A WARM store replays a page deterministically (no subprocess). A COLD /
offline lookup returns ``None`` — the caller makes NO subprocess call, so the
deterministic offline sweep stays byte-identical. Re-verify (do NOT re-trust)
across a device / version change — the pins are part of the content address.

──────────────────────────────────────────────────────────────────────────────
THE VERIFY GATE (the discipline — this is the point)
──────────────────────────────────────────────────────────────────────────────
A MinerU cell grid is a CANDIDATE. :func:`verify_mineru_table_textlayer`
corroborates each cell against the BORN-DIGITAL text layer of the page region
(free, deterministic, NON-Qwen → independent of MinerU's glyph priors): a cell
graduates to ``cell_exact`` ONLY when the text layer carries a contiguous run
equal to the cell text modulo the SAME legally-inert quotient the op-equivalence
stages use (:func:`lawvm.finland.op_equivalence.text_equivalence`); otherwise it
is a TYPED :class:`~lawvm.tools.fi_appendix_structure.TableCellDivergence`
(``witness_disagreement``) — the ``Å→Ä`` error surfaces as a typed divergence,
NEVER silently graduated. A page with no text layer (scanned) corroborates
nothing → every cell is ``no_witness`` (deferred, never forced). The verdict
reuses the appendix lane's own ``TableVerification`` / ``TableCellDivergence`` /
``table_escalation_route`` — no reinvented divergence type.

Discipline (AGENTS.md §1.9/§1.10): typed frozen carriers; tuple children never
list; the HTML parse type-DEFERS a span shape it cannot represent
(:class:`UnrepresentableSpan`) rather than faking a grid; the heavy subprocess is
the sole impure seam and is NEVER exercised in CI (tests inject a stub). Default
posture is OFF: with no store and no live flag this module makes no subprocess
call and produces nothing, so the appendix lane is byte-identical to today.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional, Sequence, Tuple, cast

from lawvm.finland.op_equivalence import text_equivalence
from lawvm.ingest.llm_backends.prompt_fingerprint import prompt_fingerprint

#: Default external mineru venv location (OUTSIDE the repo, gitignored). Overridable
#: via ``LAWVM_MINERU_VENV``. The weights (~2.2 GB) + env (~1.3 GB) never enter the repo.
MINERU_VENV_DEFAULT = os.path.expanduser("~/.cache/lawvm/mineru_env")

#: Default content-addressed MinerU output store (a farchive, sibling to the other
#: derived / recovered-text stores). Gitignored; a cold store yields no subprocess call.
MINERU_TABLE_STORE_DEFAULT = "data/fi_mineru_tables.farchive"

#: The model the assessment exercised. A model bump re-keys the store (part of the pin
#: fingerprint), so a stale read is never served under a superseded model contract.
MINERU_MODEL_ID_DEFAULT = "MinerU2.5-Pro-2605-1.2B"


# --------------------------------------------------------------------------- #
# Determinism pins → content-address fingerprint                              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MineruPins:
    """The numerics-affecting pins folded into the content address (AGENTS.md §1.3).

    bf16 output can differ across these, so a change to ANY of them must re-key the
    store (re-verify, never re-trust). ``transformers_version`` is included because
    the inference stack version perturbs decoding.
    """

    model_id: str = MINERU_MODEL_ID_DEFAULT
    device: str = "cpu"
    dtype: str = "bf16"
    transformers_version: str = ""

    def fingerprint(self) -> str:
        """Short deterministic fingerprint over the pins (reuses the canonical composer)."""
        return prompt_fingerprint(
            self.model_id, self.device, self.dtype, self.transformers_version
        )


# --------------------------------------------------------------------------- #
# Typed carriers (frozen; tuple children)                                     #
# --------------------------------------------------------------------------- #


class UnrepresentableSpan(ValueError):
    """A MinerU table HTML whose span shape cannot be lowered into a rectangular grid.

    A typed DEFER (AGENTS.md §1.10), never a faked grid: an overlapping / negative /
    non-integer span, or a cell landing on an already-occupied position, raises this so
    the caller records a type-deferred table rather than silently inventing cell content.
    """


@dataclass(frozen=True, slots=True)
class MineruCell:
    """One logical MinerU grid cell at its resolved TOP-LEFT ``(row, col)`` origin.

    ``rowspan`` / ``colspan`` (>=1) record the cell's footprint FAITHFULLY — a spanning
    header is placed ONCE at its origin and is NOT duplicated across the covered cells
    (that would fake content). ``is_header`` is the ``<th>`` / MinerU header flag.
    """

    row: int
    col: int
    rowspan: int
    colspan: int
    text: str
    is_header: bool = False


@dataclass(frozen=True, slots=True)
class MineruTable:
    """A MinerU table: the resolved logical cell grid + provenance.

    ``cells`` are logical cells at their top-left origins (spans recorded, not expanded).
    ``n_rows`` is the ``<tr>`` count; ``n_cols`` is the widest column reached
    (``max(col + colspan)``). ``bbox`` is MinerU's own table bbox in ITS coordinate
    system (kept verbatim for provenance; NOT used as a PDF-point cell witness — the
    verify gate reads the text layer, and per-cell bboxes are not emitted by MinerU).
    """

    locator: str
    page_num: int
    table_index: int
    n_rows: int
    n_cols: int
    caption: str
    cells: Tuple[MineruCell, ...]
    bbox: Optional[Tuple[float, float, float, float]] = None


# --------------------------------------------------------------------------- #
# PURE: MinerU table HTML → logical cell grid (rowspan/colspan occupancy)      #
# --------------------------------------------------------------------------- #


class _TableGridParser(HTMLParser):
    """Walk a MinerU ``<table>`` body into logical ``(row, col, rowspan, colspan, text)``.

    Standard HTML grid-occupancy: a running ``occupied`` set carries cells claimed by an
    earlier row's ``rowspan``; each new ``<td>`` / ``<th>`` is placed at the first free
    column in its row, then its whole footprint is marked occupied. This is the ONLY way
    a nested header (``<td rowspan=3>Lääni ja kunta</td> … <td colspan=3>tukkipuu</td>``)
    lowers with faithful positions. A malformed span raises :class:`UnrepresentableSpan`.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._row: int = -1  # incremented to 0 on the first <tr>
        self._col_cursor: int = 0
        self._occupied: set[Tuple[int, int]] = set()
        self._in_cell: bool = False
        self._cur_text: List[str] = []
        self._cur_span: Tuple[int, int] = (1, 1)
        self._cur_is_header: bool = False
        self._cur_origin: Tuple[int, int] = (0, 0)
        self.cells: List[MineruCell] = []
        self.max_col: int = 0

    @staticmethod
    def _span(attrs: Sequence[Tuple[str, Optional[str]]], name: str) -> int:
        for key, val in attrs:
            if key == name:
                try:
                    n = int((val or "1").strip())
                except (TypeError, ValueError):
                    raise UnrepresentableSpan(f"non-integer {name}={val!r}")
                if n < 1:
                    raise UnrepresentableSpan(f"{name}={n} < 1")
                return n
        return 1

    def handle_starttag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        if tag == "tr":
            self._row += 1
            self._col_cursor = 0
        elif tag in ("td", "th"):
            if self._row < 0:  # a cell before any <tr>
                self._row = 0
            # advance to the first column this row not already claimed by a rowspan above
            while (self._row, self._col_cursor) in self._occupied:
                self._col_cursor += 1
            self._in_cell = True
            self._cur_text = []
            self._cur_is_header = tag == "th"
            self._cur_span = (
                self._span(attrs, "rowspan"),
                self._span(attrs, "colspan"),
            )
            self._cur_origin = (self._row, self._col_cursor)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cur_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._in_cell:
            row, col = self._cur_origin
            rowspan, colspan = self._cur_span
            for dr in range(rowspan):
                for dc in range(colspan):
                    pos = (row + dr, col + dc)
                    if pos in self._occupied:
                        raise UnrepresentableSpan(
                            f"span overlap at {pos} (r={row},c={col},"
                            f"rs={rowspan},cs={colspan})"
                        )
                    self._occupied.add(pos)
            text = "".join(self._cur_text).strip()
            self.cells.append(
                MineruCell(
                    row=row,
                    col=col,
                    rowspan=rowspan,
                    colspan=colspan,
                    text=text,
                    is_header=self._cur_is_header,
                )
            )
            self.max_col = max(self.max_col, col + colspan)
            self._col_cursor = col + colspan
            self._in_cell = False


def parse_mineru_table_html(html: str) -> Tuple[Tuple[MineruCell, ...], int, int]:
    """Parse a MinerU ``<table>`` body into ``(cells, n_rows, n_cols)`` (PURE).

    ``cells`` are logical cells at their resolved top-left ``(row, col)`` with faithful
    ``rowspan`` / ``colspan`` (never expanded/duplicated). ``n_rows`` is the ``<tr>``
    count; ``n_cols`` is the widest column reached. A span shape that cannot lower to a
    rectangular grid raises :class:`UnrepresentableSpan` (a typed defer, never a fake).
    """
    parser = _TableGridParser()
    parser.feed(html)
    parser.close()
    n_rows = parser._row + 1 if parser.cells else 0
    return tuple(parser.cells), n_rows, parser.max_col


def _as_bbox(
    raw: object,
) -> Optional[Tuple[float, float, float, float]]:
    """Coerce a MinerU ``[x0,y0,x1,y1]`` bbox to a float 4-tuple (None if malformed)."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    vals = [cast("str | float | int", x) for x in cast("Sequence[object]", raw)]
    try:
        return (float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3]))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class DeferredMineruTable:
    """A MinerU table block whose HTML could not be lowered — recorded, never faked."""

    locator: str
    page_num: int
    table_index: int
    reason: str


def mineru_tables_from_content_list(
    content_list: Sequence[object],
    *,
    locator: str,
    page_offset: int = 1,
) -> Tuple[Tuple[MineruTable, ...], Tuple[DeferredMineruTable, ...]]:
    """Lower a MinerU ``*_content_list.json`` into ``(tables, deferred)`` (PURE).

    Every ``type == "table"`` block's ``table_body`` HTML is parsed into a
    :class:`MineruTable`; a block whose span shape cannot be represented becomes a typed
    :class:`DeferredMineruTable` (never dropped, never faked). ``page_num`` = the block's
    0-indexed ``page_idx`` + ``page_offset`` (MinerU is 0-indexed; the appendix IR is
    1-indexed). ``table_index`` is assigned in document order over the TABLE blocks.
    """
    tables: List[MineruTable] = []
    deferred: List[DeferredMineruTable] = []
    t_idx = 0
    for raw_block in content_list:
        if not isinstance(raw_block, dict):
            continue
        block = cast("dict[str, object]", raw_block)
        if block.get("type") != "table":
            continue
        page_num = int(cast("int", block.get("page_idx", 0))) + page_offset
        body = str(block.get("table_body", "") or "")
        caption_raw = block.get("table_caption", [])
        caption = (
            " ".join(str(c) for c in caption_raw)
            if isinstance(caption_raw, (list, tuple))
            else str(caption_raw or "")
        ).strip()
        try:
            cells, n_rows, n_cols = parse_mineru_table_html(body)
        except UnrepresentableSpan as exc:
            deferred.append(
                DeferredMineruTable(
                    locator=locator,
                    page_num=page_num,
                    table_index=t_idx,
                    reason=f"unrepresentable_span: {exc}",
                )
            )
            t_idx += 1
            continue
        tables.append(
            MineruTable(
                locator=locator,
                page_num=page_num,
                table_index=t_idx,
                n_rows=n_rows,
                n_cols=n_cols,
                caption=caption,
                cells=cells,
                bbox=_as_bbox(block.get("bbox")),
            )
        )
        t_idx += 1
    return tuple(tables), tuple(deferred)


# --------------------------------------------------------------------------- #
# PURE: MinerU table → appendix-lane StructuredTable IR                       #
# --------------------------------------------------------------------------- #


def lower_mineru_table(table: MineruTable):  # noqa: ANN201 (return type is the lane's IR)
    """Lower a :class:`MineruTable` into the appendix lane's ``StructuredTable`` IR (PURE).

    Each logical cell is placed ONCE at its top-left ``(row, col)`` (spans recorded on the
    :class:`MineruTable`, never expanded into duplicate cells). ``bbox`` is ``None`` on
    every cell — MinerU emits no per-cell geometry, and faking one would be slop; the
    verify gate corroborates against the page text layer instead. Imported here (not at
    module top) so this module does not hard-couple to the tools layer at import time.
    """
    from lawvm.tools.fi_appendix_structure import StructuredCell, StructuredTable

    cells = tuple(
        StructuredCell(
            row=c.row,
            col=c.col,
            text=c.text,
            is_header=c.is_header,
            bbox=None,
        )
        for c in table.cells
    )
    return StructuredTable(
        locator=table.locator,
        page_num=table.page_num,
        table_index=table.table_index,
        n_rows=table.n_rows,
        n_cols=table.n_cols,
        caption=table.caption,
        cells=cells,
    )


# --------------------------------------------------------------------------- #
# THE VERIFY GATE: corroborate each cell against the born-digital text layer   #
# --------------------------------------------------------------------------- #

#: descriptor on a MinerU cell divergence the independent text layer did not corroborate.
MINERU_TEXT_LAYER_ABSENT = "text_layer_absent"


def _corroborated(region_tokens: Sequence[str], cell_text: str) -> bool:
    """True iff the text layer carries a contiguous run equal to ``cell_text`` (PURE).

    ``region_tokens`` is the born-digital page region text, whitespace-split. A cell
    corroborates iff SOME contiguous window of that many tokens is
    :func:`text_equivalence`-equal to the cell (the SAME legally-inert quotient the
    op-equivalence stages use — diacritic- and case-SENSITIVE, so ``Å→Ä`` does NOT
    corroborate). An empty cell is vacuously corroborated. Fast path: an exact contiguous
    string match short-circuits before the quotient scan, so the O(n) ``text_equivalence``
    window scan runs only for the (few) cells that do not byte-match — the interesting
    divergences.
    """
    ctoks = cell_text.split()
    if not ctoks:
        return True  # empty spacer cell: nothing to corroborate
    k = len(ctoks)
    n = len(region_tokens)
    if k > n:
        return False
    cell_join = " ".join(ctoks)
    # Fast exact contiguous window match (no quotient) — the common case.
    for i in range(n - k + 1):
        if " ".join(region_tokens[i : i + k]) == cell_join:
            return True
    # Quotient-aware fallback (only reached when no window byte-matches): confirms a real
    # divergence, or rescues a benign whitespace/hyphen/soft-hyphen difference.
    for i in range(n - k + 1):
        if text_equivalence(" ".join(region_tokens[i : i + k]), cell_text).equal:
            return True
    return False


def verify_mineru_table_textlayer(table: MineruTable, region_text: str):  # noqa: ANN201
    """VERIFY GATE: corroborate each MinerU cell against the born-digital text layer.

    Returns the appendix lane's own ``TableVerification`` (reused, not reinvented). Each
    non-empty cell whose content the independent text layer reproduces (a contiguous
    :func:`text_equivalence`-equal run) is ``cell_exact``; otherwise a typed
    ``TableCellDivergence`` carrying the MinerU candidate text and the
    :data:`MINERU_TEXT_LAYER_ABSENT` descriptor — the ``Å→Ä`` / ``É`` glyph errors land
    here, NEVER silently graduated.

    SPARSE/SCANNED GUARD: when ``region_text`` has no tokens (a scanned page — no
    born-digital layer) NOTHING corroborates; every cell is counted ``no_witness``
    (deferred), never a forced divergence and never a graduation (the text-layer leg is
    the ONLY independent leg here, and it is absent). This mirrors the appendix lane's
    born-digital guard. Empty MinerU cells are vacuously exact.
    """
    from lawvm.tools.fi_appendix_structure import (
        TableCellDivergence,
        TableVerification,
    )

    region_tokens = region_text.split()
    have_layer = bool(region_tokens)
    n_exact = 0
    n_no_witness = 0
    divergences: List["TableCellDivergence"] = []
    for c in table.cells:
        if not c.text.strip():
            n_exact += 1  # empty spacer: vacuously exact (matches verify_table_exact)
            continue
        if not have_layer:
            n_no_witness += 1  # no independent leg on a scanned page → deferred
            continue
        if _corroborated(region_tokens, c.text):
            n_exact += 1
        else:
            divergences.append(
                TableCellDivergence(
                    row=c.row,
                    col=c.col,
                    docling_text=c.text,  # the MinerU candidate content (producer-neutral field)
                    witness_text="",  # membership check: the candidate is ABSENT from the layer
                    descriptor=MINERU_TEXT_LAYER_ABSENT,
                )
            )
    return TableVerification(
        locator=table.locator,
        page_num=table.page_num,
        table_index=table.table_index,
        n_cells=len(table.cells),
        n_exact=n_exact,
        n_no_witness=n_no_witness,
        divergences=tuple(divergences),
    )


# --------------------------------------------------------------------------- #
# Content-addressed store (determinism firewall — same pattern as the others) #
# --------------------------------------------------------------------------- #


def mineru_table_locator(artifact_digest: str, page_index: int, fingerprint: str) -> str:
    """Content-addressed key: ``mineru/<digest>/<pin-fingerprint>/page/<NNNN>``.

    The SOURCE artifact page (so two callers of the same page collide on one record),
    under the pin fingerprint (a model / device / dtype / transformers-version change
    writes a NEW keyed record, never overwriting the old — versioned + auditable, exactly
    like the recovered-text and parsed-IR stores).
    """
    return f"mineru/{artifact_digest}/{fingerprint}/page/{page_index:04d}"


class MineruTableStore:
    """A farchive of raw MinerU ``content_list`` outputs, content-addressed by source × page × pins.

    Stores the RAW deterministic MinerU output (the ``*_content_list.json`` blocks) so a
    warm replay re-derives the same tables via the PURE lowering — the immutable-source /
    mutable-derived firewall. A cold ``get`` returns ``None`` (the caller makes NO
    subprocess call). Sibling to ``data/fi_recovered_text.farchive`` /
    ``data/fi_parsed_ir.farchive``; gitignored.
    """

    def __init__(self, path: str = MINERU_TABLE_STORE_DEFAULT) -> None:
        from farchive import Farchive

        self._fa = Farchive(path)
        self.path = path

    def get(
        self, artifact_digest: str, page_index: int, fingerprint: str
    ) -> Optional[List[object]]:
        """The stored ``content_list`` for ``(digest, page, pins)``, or ``None`` (cold)."""
        span = self._fa.resolve(
            mineru_table_locator(artifact_digest, page_index, fingerprint)
        )
        if span is None:
            return None
        data = self._fa.read(span.digest)
        if data is None:
            return None
        obj = json.loads(data.decode("utf-8"))
        return obj if isinstance(obj, list) else []

    def put(
        self,
        artifact_digest: str,
        page_index: int,
        fingerprint: str,
        content_list: Sequence[object],
    ) -> str:
        """Persist one page's raw MinerU ``content_list`` (deterministic JSON); returns the digest."""
        return self._fa.store(
            mineru_table_locator(artifact_digest, page_index, fingerprint),
            json.dumps(list(content_list), ensure_ascii=False, sort_keys=True).encode(
                "utf-8"
            ),
            storage_class="mineru_content_list",
            metadata={
                "source_digest": artifact_digest,
                "page_index": str(page_index),
                "fingerprint": fingerprint,
            },
        )

    def close(self) -> None:
        self._fa.close()


# --------------------------------------------------------------------------- #
# The FIREWALLED subprocess producer (the sole impure seam — never run in CI)  #
# --------------------------------------------------------------------------- #


class MineruUnavailable(RuntimeError):
    """Raised when a LIVE MinerU run is requested but the external py3.12 venv is absent.

    A typed capability gap (AGENTS.md §1.10), never a silent empty: "the mineru venv is
    not set up" and "MinerU read this page as blank" are DIFFERENT facts the caller must
    distinguish.
    """


class MineruProducer:
    """Firewalled MinerU producer: warm-store replay + an opt-in LIVE subprocess.

    ``propose_page`` returns a page's ``content_list`` blocks. It ALWAYS consults the
    content-addressed store first; on a hit it replays deterministically with NO
    subprocess. On a miss it makes a subprocess call ONLY when ``live=True`` (the default
    ``live=False`` returns ``None`` — the cold/offline path, so the deterministic sweep is
    byte-identical). The subprocess (writing the page PDF to a temp file, running the
    external ``mineru`` CLI CPU-only from a clean cwd, reading ``*_content_list.json``) is
    the ONLY impure code and is NEVER exercised in CI — tests inject the parsed
    ``content_list`` directly through the pure functions above.
    """

    def __init__(
        self,
        *,
        store: Optional[MineruTableStore] = None,
        pins: Optional[MineruPins] = None,
        venv_path: Optional[str] = None,
    ) -> None:
        self._store = store
        self._pins = pins or MineruPins()
        self._venv_path = venv_path or os.environ.get(
            "LAWVM_MINERU_VENV", MINERU_VENV_DEFAULT
        )

    @property
    def producer_id(self) -> str:
        return "mineru"

    def _binary(self) -> str:
        return os.path.join(self._venv_path, "bin", "mineru")

    def is_available(self) -> bool:
        """True iff the external mineru venv binary exists (the firewall gate)."""
        return os.path.isfile(self._binary())

    def propose_page(
        self,
        pdf_bytes: bytes,
        page_index: int,
        artifact_digest: str,
        *,
        live: bool = False,
    ) -> Optional[List[object]]:
        """This page's MinerU ``content_list`` blocks (store-replayed, else optionally live).

        Store hit → replay (no subprocess). Cold + ``live=False`` → ``None`` (no subprocess,
        byte-identical offline). Cold + ``live=True`` → run the firewalled subprocess, persist,
        return. ``page_index`` is 0-indexed on the SOURCE artifact (the store key).
        """
        fp = self._pins.fingerprint()
        if self._store is not None:
            cached = self._store.get(artifact_digest, page_index, fp)
            if cached is not None:
                return cached
        if not live:
            return None  # COLD offline: never a subprocess call
        content_list = self._run_subprocess(pdf_bytes)
        if self._store is not None:
            self._store.put(artifact_digest, page_index, fp, content_list)
        return content_list

    def _run_subprocess(self, pdf_bytes: bytes) -> List[object]:
        """Run the external mineru CLI CPU-only on ``pdf_bytes`` and read its content_list.

        THE FIREWALL BOUNDARY — the only impure code in this module, never run in CI. Writes
        the PDF to a temp file, invokes ``<venv>/bin/mineru -p <pdf> -o <out> -b vlm-engine``
        from a CLEAN cwd (a stray ``profile.py`` shadows stdlib ``profile``), CPU-only so it
        never contends with the :8080 GPU, and returns the parsed ``*_content_list.json``.
        """
        import subprocess
        import tempfile

        if not self.is_available():
            raise MineruUnavailable(
                f"mineru venv not found at {self._venv_path!r}; set up the external "
                "py3.12 venv (see the module docstring) or LAWVM_MINERU_VENV"
            )
        with tempfile.TemporaryDirectory() as work:
            pdf_path = os.path.join(work, "page.pdf")
            out_dir = os.path.join(work, "out")
            with open(pdf_path, "wb") as fh:
                fh.write(pdf_bytes)
            env = dict(os.environ)
            env["MINERU_MODEL_SOURCE"] = env.get("MINERU_MODEL_SOURCE", "huggingface")
            env["MINERU_DEVICE_MODE"] = self._pins.device
            subprocess.run(
                [self._binary(), "-p", pdf_path, "-o", out_dir, "-b", "vlm-engine"],
                cwd=work,  # clean cwd: no profile.py shadow
                env=env,
                check=True,
                capture_output=True,
            )
            content_path = os.path.join(
                out_dir, "page", "vlm", "page_content_list.json"
            )
            with open(content_path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            return obj if isinstance(obj, list) else []


# --------------------------------------------------------------------------- #
# Opt-in additive lane: one PDF's MinerU tables + their text-layer verdicts     #
# --------------------------------------------------------------------------- #


def mineru_tables_for_page(
    content_list: Sequence[object],
    region_text: str,
    *,
    locator: str,
    page_offset: int = 1,
):  # noqa: ANN201 (returns lane IR tuples)
    """Lower + VERIFY one page's MinerU tables (PURE; the additive-lane core).

    Given an already-produced ``content_list`` (from the store or a stub) and the page's
    born-digital ``region_text``, returns ``(structured_tables, verifications, routes,
    deferred)`` where each table is a ``StructuredTable``, each verification a
    ``TableVerification`` from the VERIFY GATE, and each route a
    ``table_escalation_route`` verdict (``self_verified`` / ``vision_escalate`` /
    ``no_witness_deferred``). Span shapes that could not be represented are returned as
    typed :class:`DeferredMineruTable`. No subprocess, no PDF, no network — hermetically
    testable; the production caller supplies the store-replayed ``content_list`` and the
    pdfium page text.
    """
    from lawvm.tools.fi_appendix_structure import table_escalation_route

    tables, deferred = mineru_tables_from_content_list(
        content_list, locator=locator, page_offset=page_offset
    )
    structured = tuple(lower_mineru_table(t) for t in tables)
    verifications = tuple(
        verify_mineru_table_textlayer(t, region_text) for t in tables
    )
    routes = tuple(table_escalation_route(v) for v in verifications)
    return structured, verifications, routes, deferred
