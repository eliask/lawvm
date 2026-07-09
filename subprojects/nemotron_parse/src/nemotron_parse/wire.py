"""The frozen client<->service wire format: compact ``KIND: text`` blocks.

PURE module — no heavy imports, no model, no I/O. This is the half of the
process boundary the service owns; the main package's
``lawvm.finland.llm_backends.vision_producer._parse_blocks`` owns the other
half. The format is the one that parser already understands:

- one block per region in reading order, ``LABEL: text``;
- a block's text may wrap onto following lines; a new block starts only at the
  next governed label;
- governed labels: HEADING, PARA, ITEM, TABLE, FOOTNOTE — nothing else is ever
  emitted (no JSON, no markdown, no commentary).

Nemotron-Parse's native semantic classes are mapped through
``NEMOTRON_CLASS_TO_WIRE``; a class outside the mapping (page furniture,
pictures, formulas) is DROPPED, never relabeled — the same governed-vocabulary
discipline the vision producer applies to model-invented kinds.

The golden for this format is ``tests/data/wire_contract_golden.txt``, pinned
from both sides of the process boundary (see README).
"""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence, Tuple

#: Governed wire labels, the ONLY block heads that may appear on stdout.
#: Mirrors ``vision_producer._VISION_KINDS`` keys — change NEITHER alone.
GOVERNED_WIRE_KINDS: Tuple[str, ...] = ("HEADING", "PARA", "ITEM", "TABLE", "FOOTNOTE")

#: Nemotron-Parse semantic class -> governed wire label. Classes absent here
#: are dropped (Page-header, Page-footer, Picture, Formula, Caption, TOC, ...):
#: page furniture and non-text regions are noise for statute ingest, and
#: relabeling would launder an ungoverned kind into the vocabulary.
#: Verify the class inventory against the model card when deploying.
NEMOTRON_CLASS_TO_WIRE: Mapping[str, str] = {
    "Title": "HEADING",
    "Section-header": "HEADING",
    "Text": "PARA",
    "List-item": "ITEM",
    "Table": "TABLE",
    "Footnote": "FOOTNOTE",
}


def emit_kind_blocks(regions: Sequence[Tuple[str, str]]) -> str:
    """Render ``(nemotron_class, text)`` regions (reading order) as wire blocks.

    Empty-text regions and unmapped classes are dropped. Multi-line region text
    is emitted as-is: the parser joins wrapped lines back into one block. (A
    text LINE that itself begins ``HEADING:`` etc. would be re-framed by the
    parser — an accepted hazard of the pre-existing compact format; the wire
    format is frozen, not extended, here.)
    """
    lines: list[str] = []
    for cls, text in regions:
        label = NEMOTRON_CLASS_TO_WIRE.get(cls)
        if label is None:
            continue
        body = text.strip()
        if not body:
            continue
        lines.append(f"{label}: {body}")
    return "\n".join(lines) + ("\n" if lines else "")


def wire_labels_used(blocks: str) -> Tuple[str, ...]:
    """The governed labels present in emitted wire text (self-audit helper)."""
    seen: list[str] = []
    for line in blocks.splitlines():
        head, sep, _ = line.partition(":")
        if sep and head in GOVERNED_WIRE_KINDS and head not in seen:
            seen.append(head)
    return tuple(seen)


def assert_wire_clean(blocks: str, known_heads: Iterable[str] = GOVERNED_WIRE_KINDS) -> None:
    """Raise ``ValueError`` if a top-level block head is outside the vocabulary.

    Used by the serve CLI as a last-line output guard: whatever the model or
    mapping does, stdout never carries an ungoverned block head.
    """
    known = set(known_heads)
    in_block = False
    for line in blocks.splitlines():
        head, sep, _ = line.partition(":")
        if sep and head in known:
            in_block = True
            continue
        if not in_block and line.strip():
            raise ValueError(f"wire output starts with an ungoverned line: {line[:80]!r}")
