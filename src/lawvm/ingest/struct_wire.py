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
    # Level-1 freeform escape hatches (§1 / §5.5). Bbox-anchored (``V<bbox>`` src)
    # + inline literal; VERBATIM carries a closed ``#reason``. Rate-limited by
    # construction: a clean page emits ZERO freeform nodes (stays output-sparse).
    "MATH": SourceDocumentNodeKind.MATH_REGION,
    "VERBATIM": SourceDocumentNodeKind.VERBATIM_REGION,
}

# The closed ``#reason`` vocabulary a VERBATIM (or MATH) freeform region may carry
# (mirrors ``ingest.metadata._FREEFORM_REASONS`` — kept in sync, imported there is
# a cycle so it is duplicated intentionally and asserted equal in a hermetic test).
_FREEFORM_REASONS = frozenset(
    {
        "marginalia",
        "complex_layout",
        "image_baked",
        "garbled_source",
        "ambiguous",
        "rotated",
        "handwritten",
    }
)

# The kinds that MAY carry a ``V<bbox>`` freeform src + a ``#reason``.
_FREEFORM_KINDS = frozenset(
    {SourceDocumentNodeKind.MATH_REGION, SourceDocumentNodeKind.VERBATIM_REGION}
)


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
class FreeformSpec:
    """A freeform escape-hatch region's bbox + reason (MATH / VERBATIM nodes).

    ``bbox`` is the ``V<bbox>`` PDF-point anchor (never pixel-copied); ``reason``
    is a member of the closed ``_FREEFORM_REASONS`` vocabulary. The node's inline
    literal (the faithful math/verbatim transcription) lives in ``StructBuildNode.text``.
    """

    bbox: Tuple[float, float, float, float]
    reason: str


@dataclass(frozen=True, slots=True)
class StructBuildNode:
    """A nascent tree node from the build wire — tier-free, text span-copied.

    ``kind`` is a governed ``SourceDocumentNodeKind``; ``text`` is the code-copied
    span (empty for a pure container / an image; the inline literal for a freeform
    region); ``image`` names the referenced embedded image when the node is an
    IMAGE; ``freeform`` carries the bbox+reason for a MATH / VERBATIM escape
    hatch; ``children`` are assembled in the model's sibling-emission order.
    """

    kind: SourceDocumentNodeKind
    text: str = ""
    image: Optional[ImageElement] = None
    freeform: Optional[FreeformSpec] = None
    children: Tuple["StructBuildNode", ...] = ()


@dataclass(frozen=True, slots=True)
class StructBuildResult:
    """The assembled per-page forest + accounting findings (nothing silently lost)."""

    roots: Tuple[StructBuildNode, ...]
    findings: Tuple[str, ...] = ()
    terminator_used: bool = False
    total_command_lines: int = 0
    terminated_command_lines: int = 0
    patches_applied: int = 0
    node_patches_applied: int = 0


@dataclass(frozen=True, slots=True)
class NodePatch:
    """A node-addressed structural PATCH — DELETE a node+subtree or RELABEL its kind.

    Milestone-2 structural PATCH (§5 Decision 1). Unlike the line-level text PATCH
    (an addressed correction to the numbered lines, applied BEFORE span-copy), a
    node PATCH is a TREE-level op collected here and applied AFTER assembly:

      ``PATCH 0 N7:``          delete node 7 and its whole subtree (empty inline)
      ``PATCH 0 N7: SECTION``  relabel node 7 to the governed kind ``SECTION``

    ``kind`` is ``None`` for a delete; a governed ``SourceDocumentNodeKind`` for a
    relabel. The address space is the wire's own node ids in ``parse_struct_wire``;
    in the converge refine loop it is the 1-based text-leaf line index (see
    ``page_level._apply_delta_wire``). A bad id / un-governed relabel kind is
    DROPPED with a typed finding (existing hygiene), never a silent no-op / crash.
    """

    node_id: int
    kind: Optional[SourceDocumentNodeKind]  # None = delete; else relabel target


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
    reason_token: str = ""  # freeform ``#reason`` (MATH/VERBATIM head only)


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
    """Parse ``<id> <kind> <parent> <src> [#reason] [: inline]`` — or ``None``.

    No regex. The head is 4 whitespace-separated tokens (a digit id, a kind
    token, a digit parent, a src token), OPTIONALLY a 5th ``#reason`` token for a
    freeform ``V<bbox>`` head (the ONE head-shape change, §5 Decision — allow the
    freeform head). Any OTHER shape is dropped by the caller (never guessed).
    """
    head, inline = _split_head_inline(unit)
    parts = head.split()
    if len(parts) not in (4, 5):
        return None
    id_tok, kind_tok, parent_tok, src_tok = parts[:4]
    reason_tok = parts[4] if len(parts) == 5 else ""
    if not id_tok.isdigit() or not parent_tok.isdigit():
        return None
    # A 5th token is ONLY legal as a ``#reason`` on a freeform ``V<bbox>`` src.
    if reason_tok and not reason_tok.startswith("#"):
        return None
    return _RawCommand(
        node_id=int(id_tok),
        kind_token=kind_tok.upper(),
        parent_id=int(parent_tok),
        src_token=src_tok,
        inline_text=inline,
        reason_token=reason_tok,
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


def _parse_node_ref(token: str) -> Optional[int]:
    """``N7`` → node id 7 (a structural-PATCH address); malformed → ``None``.

    The ``N<id>`` src addresses a NODE (not a reading-order line): a node PATCH
    deletes / relabels it. The id space is the wire's own node ids (in
    ``parse_struct_wire``) or the 1-based text-leaf line index (in the converge
    refine loop) — the caller resolves it; this only parses the token shape.
    """
    if not token.startswith("N"):
        return None
    body = token[1:]
    return int(body) if body.isdigit() else None


def _parse_node_patch(cmd: "_RawCommand") -> Optional[Tuple[int, Optional[str]]]:
    """A ``PATCH 0 N<id>[: KIND]`` node op → ``(node_id, kind_token_or_None)``.

    Returns ``None`` when the PATCH src is NOT an ``N<id>`` node ref (it is then a
    line/char text PATCH, handled by ``_apply_patches``). An empty inline is a
    DELETE (``kind`` None); a non-empty inline is a RELABEL to that KIND token
    (validated against the governed vocabulary by the caller — an un-governed
    relabel is dropped with a finding).
    """
    node_id = _parse_node_ref(cmd.src_token)
    if node_id is None:
        return None
    relabel = cmd.inline_text.strip()
    return (node_id, relabel.upper() if relabel else None)


def _parse_freeform_bbox(token: str) -> Optional[Tuple[float, float, float, float]]:
    """``V10,20,110,70`` → the freeform region bbox (x0,y0,x1,y1); malformed → None.

    The ``V`` src token bbox-anchors a MATH / VERBATIM freeform region in PDF
    points (never pixel-copied). Four comma-separated floats with ``x1>=x0`` and
    ``y1>=y0``; any other shape is malformed → the node is dropped by the caller.
    """
    if not token.startswith("V"):
        return None
    body = token[1:]
    parts = body.split(",")
    if len(parts) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(p) for p in parts)
    except ValueError:
        return None
    if x1 < x0 or y1 < y0:
        return None
    return (x0, y0, x1, y1)


def _resolve_freeform(
    src_token: str, reason_token: str, inline_text: str
) -> Tuple[Optional[FreeformSpec], str, Optional[str]]:
    """Resolve a freeform ``V<bbox> [#reason]`` head → (spec, literal, finding).

    The bbox comes from ``V<bbox>`` (required, PDF points); the reason from an
    optional ``#reason`` (defaults to ``ambiguous`` when omitted, so a bare ``V``
    head is still faithful — the escape hatch never silently drops content), which
    MUST be in the closed vocabulary; the faithful literal is the inline payload.
    A malformed bbox / out-of-vocab reason yields a ``finding`` (node dropped).
    """
    bbox = _parse_freeform_bbox(src_token)
    if bbox is None:
        return None, "", f"malformed freeform V-bbox: {src_token}"
    reason = reason_token[1:] if reason_token.startswith("#") else reason_token
    if not reason:
        reason = "ambiguous"
    if reason not in _FREEFORM_REASONS:
        return None, "", f"freeform reason out of vocab: {reason!r}"
    return FreeformSpec(bbox=bbox, reason=reason), inline_text.strip(), None


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
# PATCH ops — addressed char-span corrections against the numbered lines        #
# --------------------------------------------------------------------------- #
#
# A ``PATCH`` command is NOT a tree node — it is a DELTA: an addressed correction
# to a numbered line, applied BEFORE the tree's text is span-copied. Its src is a
# char span ``L5.10-25`` (replace those chars) or a whole line ``L5`` (replace the
# line); the correction text follows in the inline payload. This is the
# output-sparse alternative to inline re-transcription: the model emits ONLY the
# deltas vs the extracted text, not a re-type. The same primitive lets a later
# pass refine a PRIOR reconstruction (the numbered lines are just the current
# state, whatever produced them) — iterated to an empty patch, it converges.
#
# Patches are single-line only, so applying them never shifts line indices other
# refs depend on; multiple char patches on one line apply RIGHT-TO-LEFT to
# preserve offsets (the mev insertion discipline).


def _apply_patches(
    lines: Sequence[str], patch_cmds: Sequence["_RawCommand"]
) -> Tuple[List[str], List[str], int]:
    """Apply PATCH deltas to a copy of ``lines`` → (patched_lines, findings, count).

    A char-span patch ``L5.10-25`` replaces chars [10,25) of line 5; a whole-line
    patch ``L5`` replaces line 5. Multi-line ``L2-5`` patches are DROPPED (they
    would shift line indices). Char patches on one line apply right-to-left.
    """
    buf = list(lines)
    findings: List[str] = []
    per_line: Dict[int, List[Tuple[Optional[int], Optional[int], str]]] = {}
    for cmd in patch_cmds:
        char = _parse_char_ref(cmd.src_token)
        if char is not None:
            ln, cs, ce = char
            per_line.setdefault(ln, []).append((cs, ce, cmd.inline_text))
            continue
        line = _parse_line_ref(cmd.src_token)
        if line is not None:
            a, b = line
            if a != b:
                findings.append(f"multi-line PATCH {cmd.src_token} dropped (would shift line indices)")
                continue
            per_line.setdefault(a, []).append((None, None, cmd.inline_text))
            continue
        findings.append(f"unresolvable PATCH src {cmd.src_token!r} dropped")

    count = 0
    for ln, ops in per_line.items():
        if not (1 <= ln <= len(buf)):
            findings.append(f"PATCH line {ln} out of range")
            continue
        s = buf[ln - 1]
        whole = [t for (a, _b, t) in ops if a is None]
        if whole:
            s = whole[-1]  # last whole-line replacement wins
            count += 1
        spans = sorted(((a, b, t) for (a, b, t) in ops if a is not None), key=lambda x: -(x[0] or 0))
        for a, b, t in spans:
            if a is not None and b is not None and 0 <= a <= b <= len(s):
                s = s[:a] + t + s[b:]
                count += 1
            else:
                findings.append(f"PATCH char span {a}-{b} out of range on line {ln}")
        buf[ln - 1] = s
    return buf, findings, count


# --------------------------------------------------------------------------- #
# Node-addressed structural PATCH — DELETE subtree / RELABEL kind (milestone 2) #
# --------------------------------------------------------------------------- #
#
# A ``PATCH 0 N<id>`` command is a TREE-level op (§5 Decision 1, milestone 2): it
# RETRACTS a duplicated / hallucinated node the model emitted earlier, or fixes a
# mis-kinded block — the ACTIVE retraction path complementing the passive
# ``unwitnessed_content`` tripwire. Distinct from the line-level text PATCH
# (``_apply_patches``, applied BEFORE span-copy with the no-line-shift invariant):
# node PATCH is applied AFTER text and collected here as ``NodePatch`` ops, resolved
# against the assembled tree by a caller-supplied ``id → node-address`` map.
#
# Discipline: monotone + order-insensitive where possible. Deletes are applied
# first (a delete removes the whole subtree; a relabel of an already-deleted node
# is then a bad-id finding, not a crash). A node id that doesn't resolve, or a
# relabel to an un-governed kind, is DROPPED with a typed finding.


def collect_node_patches(
    patch_cmds: Sequence["_RawCommand"],
) -> Tuple[List[NodePatch], List["_RawCommand"], List[str]]:
    """Split PATCH commands into node ops + line/char ops → (node_patches, line_cmds, findings).

    A PATCH whose src is ``N<id>`` is a structural node op (delete/relabel); every
    other PATCH (``L<n>`` / ``L<n>.a-b``) is a line/char text delta for
    ``_apply_patches``. An un-governed relabel KIND is dropped HERE with a finding
    (the id itself is resolved later against the tree — a bad id is dropped then).
    """
    node_patches: List[NodePatch] = []
    line_cmds: List["_RawCommand"] = []
    findings: List[str] = []
    for cmd in patch_cmds:
        parsed = _parse_node_patch(cmd)
        if parsed is None:
            line_cmds.append(cmd)
            continue
        node_id, kind_token = parsed
        if kind_token is None:
            node_patches.append(NodePatch(node_id=node_id, kind=None))
            continue
        kind = _STRUCT_KINDS.get(kind_token)
        if kind is None:
            findings.append(
                f"node PATCH N{node_id} relabel to un-governed kind {kind_token!r} dropped"
            )
            continue
        node_patches.append(NodePatch(node_id=node_id, kind=kind))
    return node_patches, line_cmds, findings


def _apply_node_patches_by_id(
    node_patches: Sequence[NodePatch],
    payloads: Dict[int, "_NodePayload"],
    parent_of: Dict[int, int],
    emission_order: List[int],
    findings: List[str],
) -> int:
    """Apply node PATCHes to the payload maps IN PLACE (delete subtree / relabel).

    Address space = the wire's own node ids. Deletes are applied first (each
    removes the target + its whole subtree from ``payloads`` / ``parent_of`` /
    ``emission_order``), then relabels. A node id that no longer resolves (never
    emitted, or already inside a deleted subtree) → a bad-id finding; a relabel is
    a payload kind swap. Returns the count of applied ops (deletes + relabels)."""
    deletes = [p.node_id for p in node_patches if p.kind is None]
    relabels = [(p.node_id, p.kind) for p in node_patches if p.kind is not None]
    applied = 0

    def _descendants(root: int) -> set:
        """The node id + every transitive descendant (via ``parent_of``)."""
        kids_of: Dict[int, List[int]] = {}
        for nid, pid in parent_of.items():
            kids_of.setdefault(pid, []).append(nid)
        out: set = set()
        stack = [root]
        while stack:
            nid = stack.pop()
            if nid in out:
                continue
            out.add(nid)
            stack.extend(kids_of.get(nid, ()))
        return out

    for nid in deletes:
        if nid not in payloads:
            findings.append(f"node PATCH N{nid} delete dropped (no such node)")
            continue
        subtree = _descendants(nid)
        for dead in subtree:
            payloads.pop(dead, None)
            parent_of.pop(dead, None)
        emission_order[:] = [n for n in emission_order if n not in subtree]
        applied += 1

    for nid, kind in relabels:
        payload = payloads.get(nid)
        if payload is None:
            findings.append(f"node PATCH N{nid} relabel dropped (no such node)")
            continue
        assert kind is not None
        payloads[nid] = _NodePayload(
            kind=kind, text=payload.text, image=payload.image, freeform=payload.freeform
        )
        applied += 1
    return applied


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
    freeform: Optional[FreeformSpec] = None


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

    # PATCH pass: a PATCH is a DELTA, never a tree node. Split into node-addressed
    # structural ops (``N<id>`` — delete subtree / relabel kind, milestone 2) and
    # line/char text ops (``L<n>`` — applied BEFORE span-copy so the tree reads the
    # corrected text). Node ops are applied to the assembled payloads/edges below.
    patch_cmds = [c for c, _t in commands if c.kind_token == "PATCH"]
    node_patches, line_patch_cmds, node_patch_findings = collect_node_patches(patch_cmds)
    findings.extend(node_patch_findings)
    lines, patch_findings, patches_applied = _apply_patches(lines, line_patch_cmds)
    findings.extend(patch_findings)

    # Pass 1: validate kind + resolve src → immutable payload; record emission order.
    payloads: Dict[int, _NodePayload] = {}
    parent_of: Dict[int, int] = {}
    emission_order: List[int] = []
    for cmd, _terminated in commands:
        if cmd.kind_token == "PATCH":
            continue  # already consumed by the PATCH pass
        kind = _STRUCT_KINDS.get(cmd.kind_token)
        if kind is None:
            findings.append(f"un-governed kind dropped: {cmd.kind_token!r}")
            continue
        if cmd.node_id in payloads:
            findings.append(f"duplicate node id dropped: {cmd.node_id}")
            continue
        # Freeform escape-hatch head (MATH / VERBATIM): a ``V<bbox> [#reason]``
        # src + inline literal — the ONE alternate head shape (§1 / §5.5).
        if kind in _FREEFORM_KINDS:
            spec, literal, ff_finding = _resolve_freeform(
                cmd.src_token, cmd.reason_token, cmd.inline_text
            )
            if ff_finding is not None:
                findings.append(f"node {cmd.node_id} dropped ({ff_finding})")
                continue
            payloads[cmd.node_id] = _NodePayload(
                kind=kind, text=literal, image=None, freeform=spec
            )
            parent_of[cmd.node_id] = cmd.parent_id
            emission_order.append(cmd.node_id)
            continue
        # A governed non-freeform kind must NOT carry a ``#reason`` token.
        if cmd.reason_token:
            findings.append(
                f"node {cmd.node_id} dropped (#reason on non-freeform {cmd.kind_token})"
            )
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

    # Node-PATCH pass (milestone 2): apply structural ops to the assembled payloads
    # BEFORE the edge map, addressing the wire's own node ids. Deletes first (a
    # delete removes the node + its whole subtree), then relabels (a relabel of an
    # already-deleted node is then a bad-id finding). Order-insensitive among
    # distinct nodes; a bad id → a typed finding, never a crash.
    node_patches_applied = _apply_node_patches_by_id(
        node_patches, payloads, parent_of, emission_order, findings
    )

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
        return StructBuildNode(
            kind=p.kind, text=p.text, image=p.image, freeform=p.freeform, children=tuple(kids)
        )

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
        patches_applied=patches_applied,
        node_patches_applied=node_patches_applied,
    )
