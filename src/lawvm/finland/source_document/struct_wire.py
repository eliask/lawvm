"""v2 span-copy wire — an EXPLICIT STRUCTURAL BUILD SCRIPT (pure, serverless).

The v1 span lane (``vision_producer._parse_span_blocks``) emits a FLAT list of
``KIND N-M`` blocks: no hierarchy, no reordering, no tables-as-trees, no images.
This module graduates the wire to a BUILD SCRIPT — one node per output line, each
line naming its own id, kind, parent, and a content REFERENCE (never re-typed
text). That buys arbitrary-depth hierarchy, free sibling reordering, tables built
row/cell by row/cell, and content-addressed embedded images — while keeping v1's
economics (the model references the free reading-order lines; text is span-copied
BY CODE) and v1's hygiene (un-governed kind dropped, out-of-range ref dropped,
0x1F-terminated commands so a payload newline is content not a boundary).

Grammar (one node per line, each terminated by the 0x1F unit separator):

    <id> <kind> <parent> <src> [: inline-text]

  * ``<id>``   — a small integer the MODEL assigns, monotonic in its own numbering.
  * ``<kind>`` — a FIXED governed vocabulary (``_STRUCT_KINDS``); un-governed → DROPPED.
  * ``<parent>`` — id of the parent node, or ``0`` for a root child. A node whose
    parent id was never emitted is re-parented to ROOT with a typed finding (no
    silent reparenting).
  * ``<src>`` — a content reference, NEVER re-typed text:
      ``L5``       whole reading-order line 5
      ``L2-5``     line span 2..5
      ``L5.10-40`` char span 10..40 within line 5 (sub-line reorder granularity)
      ``I3``       image element 3 (content-addressed; referenced, never pixel-copied)
      ``-``        pure structural container (no direct text)
      ``T``        inline transcription follows in ``: inline-text`` (image-baked text)
  * ``: inline-text`` — ONLY for ``TRANSCRIBE`` (``T`` src) or an addressed
    REPLACE-style correction; otherwise omitted and the text is span-copied.

Sibling order = EMISSION order among nodes sharing a parent, so a model fixes
broken reading order by decomposing a paragraph into per-line / per-char refs and
emitting them in the intended order. Parent links keep interleaved subtrees from
tangling, so nodes may be emitted in ANY global order.

Assurance discipline: this module assigns NO tier — it builds a naked tree of
``StructBuildNode`` carriers. The ingest lowers them to ``SourceDocumentNode`` at
the tier adjudication decides (the tree STRUCTURE is a single model claim unless
a deterministic structure witness corroborates it). Hygiene mirrors v1: a
hallucinated line/char/image ref fabricates NO text and is dropped, never clamped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from lawvm.core.source_document.ir import SourceDocumentNodeKind

# The wire's command terminator — the ASCII unit separator (0x1F). A newline (or
# a command-looking line) inside a TRANSCRIBE payload is then CONTENT, never a
# command boundary. Kept identical to v1 so the framing lesson transfers.
STRUCT_COMMAND_SEPARATOR = "\x1f"
_STRUCT_SEPARATOR_DEBUG_GLYPH = "␟"  # ␟ SYMBOL FOR UNIT SEPARATOR


def render_struct_wire_for_debug(content: str) -> str:
    """Human-displayable build wire: the raw 0x1F terminator becomes ``␟`` + newline."""
    return content.replace(STRUCT_COMMAND_SEPARATOR, _STRUCT_SEPARATOR_DEBUG_GLYPH + "\n")


# Governed build-script kinds → SourceDocumentNodeKind. Un-governed kinds are
# DROPPED (never relabeled/clamped), mirroring v1's discipline.
_STRUCT_KINDS: Mapping[str, SourceDocumentNodeKind] = {
    "SECTION": SourceDocumentNodeKind.SECTION,
    "SUBSECTION": SourceDocumentNodeKind.SUBSECTION,
    "PARA": SourceDocumentNodeKind.PARAGRAPH,
    "ITEM": SourceDocumentNodeKind.ITEM,
    "HEADING": SourceDocumentNodeKind.HEADING,
    "TABLE": SourceDocumentNodeKind.TABLE,
    "ROW": SourceDocumentNodeKind.TABLE_ROW,
    "CELL": SourceDocumentNodeKind.TABLE_CELL,
    "IMAGE": SourceDocumentNodeKind.IMAGE_REGION,
    "FOOTNOTE": SourceDocumentNodeKind.FOOTNOTE,
    "TRANSCRIBE": SourceDocumentNodeKind.PARAGRAPH,
}


@dataclass(frozen=True, slots=True)
class ImageElement:
    """One embedded image XObject offered to the model as ``{N}``.

    ``digest`` content-addresses the RAW image bytes (bit-exact, immutable);
    ``bbox`` / ``media_type`` / ``width`` / ``height`` / ``role`` are the
    intrinsic facts the IMAGE node carries in its anchor + attrs.
    """

    index: int
    digest: str
    media_type: str
    width: int
    height: int
    bbox: Tuple[float, float, float, float]
    role: str = "embedded_image"


@dataclass(frozen=True, slots=True)
class StructBuildNode:
    """A nascent tree node from the build wire — tier-free, text span-copied.

    ``kind`` is a governed ``SourceDocumentNodeKind``; ``text`` is the code-copied
    span (empty for a pure container / an image); ``image`` names the referenced
    embedded image when the node is an IMAGE; ``children`` are assembled in the
    model's sibling-emission order.
    """

    kind: SourceDocumentNodeKind
    text: str = ""
    image: Optional[ImageElement] = None
    children: Tuple["StructBuildNode", ...] = ()


@dataclass(frozen=True, slots=True)
class StructBuildResult:
    """The assembled per-page forest + accounting findings (nothing silently lost)."""

    roots: Tuple[StructBuildNode, ...]
    findings: Tuple[str, ...] = ()
    terminator_used: bool = False
    total_command_lines: int = 0
    terminated_command_lines: int = 0


# --------------------------------------------------------------------------- #
# Wire framing (0x1F terminator; lenient newline fallback)                     #
# --------------------------------------------------------------------------- #


@dataclass
class _RawCommand:
    """A parsed build-script line before tree assembly."""

    node_id: int
    kind_token: str
    parent_id: int
    src_token: str
    inline_text: str


def _split_head_inline(unit: str) -> Tuple[str, str]:
    """Split a unit into its head (before the FIRST ``: ``) and inline payload.

    Only a ``": "`` (colon+space) or a trailing bare ``:`` opens the inline
    payload, so a src token can never contain a colon and legal text like
    ``4 §:ään`` inside a span reference is impossible (src is L/I/T/-, no colon).
    """
    idx = unit.find(":")
    if idx < 0:
        return unit.strip(), ""
    return unit[:idx].strip(), unit[idx + 1 :].lstrip()


def _parse_command_line(unit: str) -> Optional[_RawCommand]:
    """Parse ``<id> <kind> <parent> <src> [: inline]`` — or ``None`` if malformed.

    No regex. The head is 4 whitespace-separated tokens: a digit id, a kind
    token, a digit parent, a src token. A line that does not present exactly that
    shape is dropped by the caller (never guessed).
    """
    head, inline = _split_head_inline(unit)
    parts = head.split()
    if len(parts) != 4:
        return None
    id_tok, kind_tok, parent_tok, src_tok = parts
    if not id_tok.isdigit() or not parent_tok.isdigit():
        return None
    return _RawCommand(
        node_id=int(id_tok),
        kind_token=kind_tok.upper(),
        parent_id=int(parent_tok),
        src_token=src_tok,
        inline_text=inline,
    )


def _struct_units(content: str) -> Tuple[Tuple[str, bool], ...]:
    """Frame the build wire into ``(unit, terminated)`` command units.

    When the 0x1F terminator is present, split ONLY on it — a payload newline or
    a command-looking line inside a TRANSCRIBE inline is content, never a
    boundary (each such unit is ``terminated=True``). When the model ignored the
    separator, fall back to lenient per-line framing (``terminated=False``):
    every non-empty line is a candidate command (generate freely, parse robustly).
    """
    if STRUCT_COMMAND_SEPARATOR in content:
        # The final segment after the last separator is an unterminated tail if
        # non-empty (the model stopped mid-command / omitted the last 0x1F).
        segments = content.split(STRUCT_COMMAND_SEPARATOR)
        units: List[Tuple[str, bool]] = []
        for i, seg in enumerate(segments):
            s = seg.strip()
            if not s:
                continue
            terminated = i < len(segments) - 1
            units.append((s, terminated))
        return tuple(units)
    return tuple((ln.strip(), False) for ln in content.splitlines() if ln.strip())


# --------------------------------------------------------------------------- #
# Source-reference resolution (line / char span / image / container / inline)  #
# --------------------------------------------------------------------------- #


def _parse_line_ref(token: str) -> Optional[Tuple[int, int]]:
    """``L5`` → (5,5); ``L2-5`` → (2,5). Reversed/garbled → ``None`` (never fixed)."""
    if not token.startswith("L"):
        return None
    body = token[1:]
    a, sep, b = body.partition("-")
    if not a.isdigit():
        return None
    start = int(a)
    if not sep:
        return (start, start)
    if not b.isdigit():
        return None
    end = int(b)
    return (start, end) if end >= start else None


def _parse_char_ref(token: str) -> Optional[Tuple[int, int, int]]:
    """``L5.10-40`` → (line 5, char 10, char 40); malformed → ``None``.

    Char offsets are 0-indexed, half-open ``[start, end)`` into the line text.
    """
    if not token.startswith("L") or "." not in token:
        return None
    line_part, _dot, span_part = token[1:].partition(".")
    if not line_part.isdigit():
        return None
    a, sep, b = span_part.partition("-")
    if not sep or not a.isdigit() or not b.isdigit():
        return None
    start, end = int(a), int(b)
    if end < start:
        return None
    return (int(line_part), start, end)


def _parse_image_ref(token: str) -> Optional[int]:
    """``I3`` → image element index 3; malformed → ``None``."""
    if not token.startswith("I"):
        return None
    body = token[1:]
    return int(body) if body.isdigit() else None


def _resolve_src_text(
    src_token: str,
    inline_text: str,
    lines: Sequence[str],
    images: Mapping[int, ImageElement],
) -> Tuple[str, Optional[ImageElement], Optional[str]]:
    """Resolve a src token to ``(text, image, finding)``.

    Returns the span-copied text (or inline literal), an image element (for
    ``I{N}``), or a ``finding`` string when the reference is out of range /
    malformed (the node is then dropped by the caller). A ``-`` container yields
    empty text and no image (valid). ``T`` yields the inline literal.
    """
    if src_token == "-":
        return "", None, None
    if src_token == "T":
        return inline_text.strip(), None, None
    # Char span within a single line (checked before whole-line: it is more specific).
    char_ref = _parse_char_ref(src_token)
    if char_ref is not None:
        line_no, cstart, cend = char_ref
        if 1 <= line_no <= len(lines):
            text = lines[line_no - 1][cstart:cend]
            if text:
                return text, None, None
            return "", None, f"empty char span {src_token}"
        return "", None, f"char ref line out of range: {src_token}"
    line_ref = _parse_line_ref(src_token)
    if line_ref is not None:
        start, end = line_ref
        if 1 <= start and end <= len(lines):
            text = "\n".join(lines[start - 1 : end]).strip()
            return text, None, None
        return "", None, f"line ref out of range: {src_token}"
    image_ref = _parse_image_ref(src_token)
    if image_ref is not None:
        img = images.get(image_ref)
        if img is not None:
            return "", img, None
        return "", None, f"image ref out of range: {src_token}"
    return "", None, f"unresolvable src token: {src_token}"


# --------------------------------------------------------------------------- #
# Tree assembly                                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _NodePayload:
    """An assembled node's tier-free payload (NO children — not a tree shadow).

    Children are held in a separate ``parent id → child ids`` edge map and the
    tree is built bottom-up into immutable ``StructBuildNode``s, so this frontend
    never grows a mutable IRNode-shadow kernel (§2.3 boundary): tree shape lives
    in edges + a pure recursive build, not in a mutated node.
    """

    kind: SourceDocumentNodeKind
    text: str
    image: Optional[ImageElement]


def parse_struct_wire(
    content: str,
    lines: Sequence[str],
    images: Sequence[ImageElement] = (),
) -> StructBuildResult:
    """Parse the v2 build-script wire into an assembled per-page forest.

    The block TEXT is span-copied from ``lines`` (1-indexed) BY THIS CODE — the
    model only references. ``images`` are the embedded image elements offered as
    ``I{N}``. Hygiene: an un-governed kind, a malformed line, an out-of-range
    line/char/image ref, and a duplicate id are each DROPPED with a typed
    finding; a node whose parent id was never emitted is re-parented to ROOT with
    a finding (no silent reparenting). Sibling order follows emission order.
    """
    image_by_index = {img.index: img for img in images}
    units = _struct_units(content)
    findings: List[str] = []

    total_cmds = 0
    terminated_cmds = 0
    commands: List[Tuple[_RawCommand, bool]] = []
    for unit, terminated in units:
        cmd = _parse_command_line(unit)
        if cmd is None:
            findings.append(f"malformed build line dropped: {unit[:60]!r}")
            continue
        total_cmds += 1
        if terminated:
            terminated_cmds += 1
        commands.append((cmd, terminated))

    # Pass 1: validate kind + resolve src → immutable payload; record emission order.
    payloads: Dict[int, _NodePayload] = {}
    parent_of: Dict[int, int] = {}
    emission_order: List[int] = []
    for cmd, _terminated in commands:
        kind = _STRUCT_KINDS.get(cmd.kind_token)
        if kind is None:
            findings.append(f"un-governed kind dropped: {cmd.kind_token!r}")
            continue
        if cmd.node_id in payloads:
            findings.append(f"duplicate node id dropped: {cmd.node_id}")
            continue
        text, image, ref_finding = _resolve_src_text(
            cmd.src_token, cmd.inline_text, lines, image_by_index
        )
        if ref_finding is not None:
            findings.append(f"node {cmd.node_id} dropped ({ref_finding})")
            continue
        payloads[cmd.node_id] = _NodePayload(kind=kind, text=text, image=image)
        parent_of[cmd.node_id] = cmd.parent_id
        emission_order.append(cmd.node_id)

    # Pass 2: build the parent→children edge map (emission order = sibling order);
    # re-parent orphans / self-parents to ROOT with a typed finding.
    child_ids: Dict[int, List[int]] = {}
    root_ids: List[int] = []
    for node_id in emission_order:
        parent_id = parent_of[node_id]
        if parent_id == 0:
            root_ids.append(node_id)
        elif parent_id in payloads and parent_id != node_id:
            child_ids.setdefault(parent_id, []).append(node_id)
        else:
            reason = "self-parent" if parent_id == node_id else "missing parent"
            findings.append(f"node {node_id} re-parented to root ({reason} {parent_id})")
            root_ids.append(node_id)

    # Pass 3: build immutable nodes bottom-up from roots. ``built`` guards against
    # a parent cycle (n1→n2→n1, neither a root) — such nodes are unreachable and
    # reported below, never silently vanished.
    built: set = set()

    def _build(nid: int) -> StructBuildNode:
        built.add(nid)
        p = payloads[nid]
        kids: List[StructBuildNode] = []
        for c in child_ids.get(nid, ()):
            if c in built:
                findings.append(f"node {c} dropped (parent cycle)")
                continue
            kids.append(_build(c))
        return StructBuildNode(kind=p.kind, text=p.text, image=p.image, children=tuple(kids))

    roots = tuple(_build(r) for r in root_ids)
    for nid in emission_order:
        if nid not in built:
            findings.append(f"node {nid} dropped (unreachable — parent cycle)")

    return StructBuildResult(
        roots=roots,
        findings=tuple(findings),
        terminator_used=STRUCT_COMMAND_SEPARATOR in content,
        total_command_lines=total_cmds,
        terminated_command_lines=terminated_cmds,
    )
