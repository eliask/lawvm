"""pdf_grammar.py — UK section/Schedule token grammar for PDF-only Acts (prototype).

The Finland PDF engine (``finland/pdf_text.py``, ``finland/pdf_layout.py``) is
reused verbatim for the *geometric* layer — pdfplumber extraction and the mutable
``_Builder`` tree-builder shape are jurisdiction-neutral.  What is Finnish-specific
is ``finland/attachment_ir.layout_to_ir_node``'s **token grammar** (``_OSA_RE``,
``_LIITE_RE``, ``_PARA_NUM_RE``, ``_ITEM_ALPHA_RE``) and its label→eId conventions.

This module is the UK analogue.  It recognises the UK King's-Printer statute spine
from OCR'd PDF text and emits **UK IR** (Body → Part → section → subsection, plus
Schedule → paragraph) with legislation.gov.uk-style **eIds** (``section-1``,
``schedule-1-paragraph-2``).  It does NOT touch the Finland path.

Grammar (validated against real tier-1 OCR, 1963–1987)
------------------------------------------------------
- ``1975 CHAPTER 4``      → act/chapter banner (metadata; not a body node)
- ``PART I`` / ``PART 1`` → Part division (roman or arabic)
- ``1.—(1) ...``          → section 1 opening with inline subsection (1)
- ``27A.—(1) ...``        → section 27A (letter-suffixed) with inline subsection
- ``(2)`` / ``(3)``       → bare subsection under the current section
- ``(a)`` / ``(b)``       → paragraph item under the current subsection/section
- ``SCHEDULE`` / ``Schedule 1`` / ``SECOND SCHEDULE`` → schedule
- ``Part I`` (inside a schedule) → schedule Part
- schedule paragraphs ``1.`` / ``2.``  → schedule paragraphs

Scope / deferred
----------------
The **C19 King's-Printer marginal-note segmentation** (side-notes such as
``Definition of partnership`` / ``Short title`` that OCR interleaves into a
section's first line) is handled by ``pdf_layout_uk.segment_uk_pdf_layout`` (an
x-coordinate banding pre-pass over pdfplumber word geometry) and bound onto
sections as headings by :func:`uk_layout_to_ir`.  The legacy inline heuristic
``_split_marginal_note`` (below) is a no-op text-only fallback for the plain
``pdftotext`` path, which lacks the x-coordinates the banding needs.  OCR
digit/letter confusions (``(b)``→``(6)``) are tolerated but not corrected.

Regex discipline (§2.4): every module-scope pattern is a single fixed-shape
classifier compiled via ``compile_classifier_regex`` (safety lint + prefilter),
mirroring ``attachment_ir.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from lawvm.core.ir import IRNode
from lawvm.core.regex_safety import compile_classifier_regex
from lawvm.core.semantic_types import IRNodeKind


# ---------------------------------------------------------------------------
# UK token grammar (module-scope classifiers)
# ---------------------------------------------------------------------------

# Act/chapter banner: "1975 CHAPTER 4" / "1971 CHAPTER 38". Year + CHAPTER +
# number. Metadata only — carries the regnal chapter number, no body content.
_CHAPTER_BANNER_RE = compile_classifier_regex(
    r"^(\d{4})\s+CHAPTER\s+(\d+)\b",
    classifier_id="lawvm.uk_legislation.pdf_grammar.chapter_banner",
)

# Part division: "PART I" / "PART 1" / "PART II" (body-level, ALL CAPS PART).
# Number is roman (I/V/X/L/C) or arabic. Optional trailing title is sliced by
# the caller from ``line[m.end():]``.
_PART_RE = compile_classifier_regex(
    r"^PART\s+([IVXLC]+|\d+)\b",
    classifier_id="lawvm.uk_legislation.pdf_grammar.part",
)

# Section opener with inline first subsection: "1.—(1) ...", "27A.—(1) ...".
# Group 1 = section number (digits + optional letter suffix, e.g. 27A);
# group 2 = the inline subsection number. The dash is an em/en/hyphen (OCR
# varies). This is the canonical UK section shape.
_SECTION_SUBSEC_RE = compile_classifier_regex(
    r"^(\d+[A-Z]?)\.[—–-]\((\d+)\)\s*",
    classifier_id="lawvm.uk_legislation.pdf_grammar.section_subsec",
)

# Section opener WITHOUT an inline subsection: "1. Text ..." (number + dot +
# space, no immediately-following "—(1)"). Used for single-provision sections.
# The negative lookahead keeps it from stealing a "1.—(1)" line.
_SECTION_PLAIN_RE = compile_classifier_regex(
    r"^(\d+[A-Z]?)\.(?![—–-]\()\s+",
    classifier_id="lawvm.uk_legislation.pdf_grammar.section_plain",
)

# Bare subsection: "(1)" / "(12)" at line start. Digits inside parens.
_SUBSEC_RE = compile_classifier_regex(
    r"^\((\d+)\)\s*",
    classifier_id="lawvm.uk_legislation.pdf_grammar.subsec",
)

# Paragraph item: "(a)" / "(b)" — single lowercase letter in parens.
_ITEM_ALPHA_RE = compile_classifier_regex(
    r"^\(([a-z])\)\s*",
    classifier_id="lawvm.uk_legislation.pdf_grammar.item_alpha",
)

# Schedule banner: "SCHEDULE" / "SCHEDULES" / "SCHEDULE 1" / "Schedule 2" /
# "THE SCHEDULE". A single fixed-shape trailing-number capture (no adjacent
# variable quantifiers — the number is required to be preceded by exactly one
# space via ``\ ``, and ``_schedule_number`` below extracts it). Ordinal-word
# schedules ("FIRST SCHEDULE") are matched by ``_ordinal_schedule_number``.
_SCHEDULE_RE = compile_classifier_regex(
    r"^(?:THE )?SCHEDULES?\b",
    classifier_id="lawvm.uk_legislation.pdf_grammar.schedule",
)

# Trailing schedule number "SCHEDULE 3" — separate single-shape classifier.
_SCHEDULE_NUM_RE = compile_classifier_regex(
    r"\bSCHEDULES?\s+(\d+)\b",
    classifier_id="lawvm.uk_legislation.pdf_grammar.schedule_num",
)

# Schedule Part (title-case, appears inside a schedule): "Part I" / "Part 1".
_SCHEDULE_PART_RE = compile_classifier_regex(
    r"^Part\s+([IVXLC]+|\d+)\b",
    classifier_id="lawvm.uk_legislation.pdf_grammar.schedule_part",
)

# Ordinal-word schedule banner ("FIRST SCHEDULE", "SECOND SCHEDULE").
_ORDINAL_SCHEDULE_WORDS = {
    "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5,
    "SIXTH": 6, "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9, "TENTH": 10,
}


def _is_caps_heading(stripped: str) -> bool:
    """ALL-CAPS heading line (mirrors ``attachment_ir._is_caps_heading``)."""
    if len(stripped) < 3:
        return False
    if not stripped.isupper():
        return False
    return any(c.isalpha() for c in stripped)


def _ordinal_schedule_number(stripped: str) -> Optional[int]:
    """Return the schedule number for an "FIRST SCHEDULE"-style banner, else None."""
    parts = stripped.split()
    if len(parts) >= 2 and parts[1].rstrip(".").upper() == "SCHEDULE":
        return _ORDINAL_SCHEDULE_WORDS.get(parts[0].upper())
    return None


# ---------------------------------------------------------------------------
# eId normalisation (legislation.gov.uk conventions)
# ---------------------------------------------------------------------------


def section_eid(section_label: str) -> str:
    """``"1"`` → ``"section-1"``; ``"27A"`` → ``"section-27A"``."""
    return f"section-{section_label}"


def subsection_eid(section_label: str, subsec_label: str) -> str:
    """``("1","2")`` → ``"section-1-2"`` (legislation.gov.uk subsection eId)."""
    return f"section-{section_label}-{subsec_label}"


def part_eid(part_label: str) -> str:
    """Part eId, roman normalised to lowercase: ``"I"`` → ``"part-I"``."""
    return f"part-{part_label}"


def schedule_eid(schedule_num: int) -> str:
    """``1`` → ``"schedule-1"``."""
    return f"schedule-{schedule_num}"


def schedule_paragraph_eid(schedule_num: int, para_label: str) -> str:
    """``(1,"2")`` → ``"schedule-1-paragraph-2"``."""
    return f"schedule-{schedule_num}-paragraph-{para_label}"


# ---------------------------------------------------------------------------
# Mutable builder (mirrors attachment_ir._Builder shape)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Builder:
    """Mutable IRNode builder — same shape as ``attachment_ir._Builder``."""

    kind: IRNodeKind
    label: Optional[str] = None
    text_parts: List[str] = field(default_factory=list)
    attrs: Dict[str, Any] = field(default_factory=dict)
    children: List["_Builder"] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(p for p in self.text_parts if p).strip()

    def append_text(self, segment: str) -> None:
        s = segment.strip()
        if s:
            self.text_parts.append(s)

    def to_ir_node(self) -> IRNode:
        return IRNode(
            kind=self.kind,
            label=self.label,
            text=self.text,
            attrs=dict(self.attrs) if self.attrs else {},
            children=tuple(c.to_ir_node() for c in self.children),
        )


# ---------------------------------------------------------------------------
# UK grammar → IR
# ---------------------------------------------------------------------------


def _text_lines_to_ir(
    lines: List[str],
    *,
    source_ref: str = "",
    positions: Optional[List[tuple[int, float]]] = None,
) -> IRNode:
    """Build a UK IR tree from already-ordered body text lines.

    Body → Part → section → subsection, plus Schedule → (Part) → paragraph.

    ``positions`` (optional, parallel to ``lines``) supplies a ``(page, y_top)``
    anchor per line.  When present, each section opener records its anchor in a
    transient ``attrs["_pdf_anchor"]`` so a downstream pass
    (:func:`_bind_marginal_notes`) can attach the vertically-nearest right-margin
    side-note as the section's ``heading``.  The transient attr is cleared by
    that pass; a caller who never binds notes sees no anchor leak because the
    layout entry point always runs the bind pass.
    """
    body = _Builder(
        IRNodeKind.BODY,
        attrs={"source_ref": source_ref, "source_lane": "pdf"} if source_ref
        else {"source_lane": "pdf"},
    )

    # Cursors into the current open containers.
    cur_part: Optional[_Builder] = None
    cur_section: Optional[_Builder] = None
    cur_subsec: Optional[_Builder] = None
    cur_leaf: Optional[_Builder] = None
    cur_schedule: Optional[_Builder] = None
    cur_sched_part: Optional[_Builder] = None
    cur_sched_para: Optional[_Builder] = None
    schedule_seq = 0
    in_schedule = False

    def section_parent() -> _Builder:
        return cur_part if cur_part is not None else body

    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        anchor = positions[idx] if positions is not None and idx < len(positions) else None

        # --- Chapter/act banner: metadata only, record on body attrs once. ---
        m = _CHAPTER_BANNER_RE.match(line)
        if m and not in_schedule:
            body.attrs.setdefault("chapter_year", m.group(1))
            body.attrs.setdefault("chapter_number", m.group(2))
            continue

        # --- Schedule banners (switch to schedule mode) --------------------
        ordinal_sched = _ordinal_schedule_number(line)
        m_sched = _SCHEDULE_RE.match(line)
        if ordinal_sched is not None or m_sched is not None:
            in_schedule = True
            m_num = _SCHEDULE_NUM_RE.search(line)
            if ordinal_sched is not None:
                schedule_seq = ordinal_sched
            elif m_num is not None:
                schedule_seq = int(m_num.group(1))
            else:
                schedule_seq += 1
            cur_schedule = _Builder(
                IRNodeKind.SCHEDULE,
                label=str(schedule_seq),
                attrs={"eId": schedule_eid(schedule_seq), "source_text": line},
            )
            body.children.append(cur_schedule)
            cur_sched_part = None
            cur_sched_para = None
            cur_leaf = None
            continue

        if in_schedule:
            # Schedule Part.
            mp = _SCHEDULE_PART_RE.match(line)
            if mp and cur_schedule is not None:
                cur_sched_part = _Builder(
                    IRNodeKind.PART,
                    label=mp.group(1),
                    attrs={"source_text": line},
                )
                cur_schedule.children.append(cur_sched_part)
                cur_sched_para = None
                cur_leaf = None
                continue
            # Schedule paragraph "1." / "2." .
            mpara = _SECTION_PLAIN_RE.match(line)
            if mpara and cur_schedule is not None:
                para_label = mpara.group(1)
                cur_sched_para = _Builder(
                    IRNodeKind.PARAGRAPH,
                    label=para_label,
                    attrs={
                        "eId": schedule_paragraph_eid(schedule_seq, para_label),
                        "source_text": line,
                    },
                )
                target = cur_sched_part if cur_sched_part is not None else cur_schedule
                target.children.append(cur_sched_para)
                cur_leaf = cur_sched_para
                rest = line[mpara.end():].strip()
                if rest:
                    cur_sched_para.append_text(rest)
                continue
            # Item under schedule paragraph.
            mi = _ITEM_ALPHA_RE.match(line)
            if mi and cur_sched_para is not None:
                item = _Builder(
                    IRNodeKind.ITEM, label=mi.group(1),
                    attrs={"source_text": line},
                )
                cur_sched_para.children.append(item)
                cur_leaf = item
                rest = line[mi.end():].strip()
                if rest:
                    item.append_text(rest)
                continue
            # Continuation / free text inside a schedule.
            if cur_leaf is not None:
                cur_leaf.append_text(line)
            elif cur_schedule is not None:
                p = _Builder(IRNodeKind.P)
                p.append_text(line)
                cur_schedule.children.append(p)
                cur_leaf = p
            continue

        # --- Body: PART division ------------------------------------------
        mpart = _PART_RE.match(line)
        if mpart:
            cur_part = _Builder(
                IRNodeKind.PART,
                label=mpart.group(1),
                attrs={"eId": part_eid(mpart.group(1)), "source_text": line},
            )
            body.children.append(cur_part)
            cur_section = cur_subsec = cur_leaf = None
            title = line[mpart.end():].strip()
            if title:
                heading = _Builder(IRNodeKind.HEADING)
                heading.append_text(title)
                cur_part.children.append(heading)
            continue

        # --- Body: section with inline subsection "1.—(1)" ----------------
        msec = _SECTION_SUBSEC_RE.match(line)
        if msec:
            sec_label, sub_label = msec.group(1), msec.group(2)
            cur_section = _Builder(
                IRNodeKind.SECTION, label=sec_label,
                attrs={"eId": section_eid(sec_label), "source_text": line},
            )
            if anchor is not None:
                cur_section.attrs["_pdf_anchor"] = anchor
            section_parent().children.append(cur_section)
            cur_subsec = _Builder(
                IRNodeKind.SUBSECTION, label=sub_label,
                attrs={"eId": subsection_eid(sec_label, sub_label)},
            )
            cur_section.children.append(cur_subsec)
            cur_leaf = cur_subsec
            body_text, marginal = _split_marginal_note(line[msec.end():])
            if marginal:
                cur_section.attrs["marginal_note"] = marginal
            if body_text:
                cur_subsec.append_text(body_text)
            continue

        # --- Body: plain section "1. Text" (no inline subsection) ---------
        msecp = _SECTION_PLAIN_RE.match(line)
        if msecp:
            sec_label = msecp.group(1)
            cur_section = _Builder(
                IRNodeKind.SECTION, label=sec_label,
                attrs={"eId": section_eid(sec_label), "source_text": line},
            )
            if anchor is not None:
                cur_section.attrs["_pdf_anchor"] = anchor
            section_parent().children.append(cur_section)
            cur_subsec = None
            cur_leaf = cur_section
            body_text, marginal = _split_marginal_note(line[msecp.end():])
            if marginal:
                cur_section.attrs["marginal_note"] = marginal
            if body_text:
                cur_section.append_text(body_text)
            continue

        # --- Body: bare subsection "(2)" ----------------------------------
        msub = _SUBSEC_RE.match(line)
        if msub and cur_section is not None:
            sub_label = msub.group(1)
            cur_subsec = _Builder(
                IRNodeKind.SUBSECTION, label=sub_label,
                attrs={"eId": subsection_eid(cur_section.label or "", sub_label)},
            )
            cur_section.children.append(cur_subsec)
            cur_leaf = cur_subsec
            rest = line[msub.end():].strip()
            if rest:
                cur_subsec.append_text(rest)
            continue

        # --- Body: item "(a)" ---------------------------------------------
        mitem = _ITEM_ALPHA_RE.match(line)
        if mitem and cur_section is not None:
            item = _Builder(
                IRNodeKind.ITEM, label=mitem.group(1),
                attrs={"source_text": line},
            )
            parent = cur_subsec if cur_subsec is not None else cur_section
            parent.children.append(item)
            cur_leaf = item
            rest = line[mitem.end():].strip()
            if rest:
                item.append_text(rest)
            continue

        # --- Continuation / free text -------------------------------------
        if cur_leaf is not None:
            cur_leaf.append_text(line)
        else:
            # Pre-body furniture (long title, enacting formula) — keep as P on
            # body so §0 total-accounting holds (no line silently dropped).
            p = _Builder(IRNodeKind.P)
            p.append_text(line)
            body.children.append(p)
            cur_leaf = p

    return body.to_ir_node()


# Words that, when a short trailing fragment of a section-opener line is title
# case, mark it as a bled-in marginal side-note rather than body prose. This is
# a heuristic strip only (the real fix is x-coordinate banding, deferred).
def _split_marginal_note(text: str) -> tuple[str, str]:
    """Best-effort split of a bled-in marginal note off a section opening line.

    C19/20 King's-Printer PDFs render the section's marginal side-note in a
    right-hand column; OCR appends it to the section's first physical line
    (e.g. ``"... orders made by the Amendments"``).  Without x-coordinate
    banding we cannot reliably cut it, so this prototype returns the whole text
    as body and an empty marginal — the seam exists (callers stash the result in
    ``attrs["marginal_note"]``) but the real segmentation is DEFERRED.
    """
    return text.strip(), ""


def pdf_text_to_uk_ir(pdf_text: str, *, source_ref: str = "") -> IRNode:
    """Parse ``pdftotext`` output of a UK PDF-only Act into UK IR.

    Pure transform: text in, :class:`IRNode` out.  Reuses ``finland/pdf_text``
    only to *produce* ``pdf_text`` upstream; this function is grammar-only.
    """
    return _text_lines_to_ir(pdf_text.splitlines(), source_ref=source_ref)


def layout_to_uk_ir(layout: Any, *, source_ref: str = "") -> IRNode:
    """Parse an ``AttachmentLayout`` (from ``finland/pdf_layout``) into UK IR.

    The geometric layer (``extract_pdf_layout``) is jurisdiction-neutral; this
    orders its body blocks by (page, y) and runs the UK grammar on the text.
    Tables/footnotes are not yet folded in (deferred with C19 segmentation).
    """
    blocks = sorted(
        layout.body_blocks, key=lambda b: (b.page_num, b.y_position)
    )
    return _text_lines_to_ir([b.text for b in blocks], source_ref=source_ref)


# ---------------------------------------------------------------------------
# C19 marginal-note binding (x-coordinate-segmented side-notes → section heading)
# ---------------------------------------------------------------------------

# A note binds to a section only when it sits within this many points below the
# section opener's top (the side-note is set level with — never far below — the
# section it heads). Larger vertical distance means the note belongs to a later
# section, so an intervening opener will claim it instead.
_NOTE_BIND_MAX_DY = 260.0


def _strip_pdf_anchor(node: IRNode) -> IRNode:
    """Return ``node`` with the transient ``_pdf_anchor`` attr removed (deep)."""
    new_children = tuple(_strip_pdf_anchor(c) for c in node.children)
    if "_pdf_anchor" in node.attrs:
        attrs = {k: v for k, v in dict(node.attrs).items() if k != "_pdf_anchor"}
        return IRNode(kind=node.kind, label=node.label, text=node.text,
                      attrs=attrs, children=new_children)
    if new_children != node.children:
        return IRNode(kind=node.kind, label=node.label, text=node.text,
                      attrs=dict(node.attrs), children=new_children)
    return node


def _collect_section_anchors(node: IRNode, out: List[tuple[int, float, str]]) -> None:
    """Collect ``(page, y_top, section_label)`` for every anchored section."""
    if node.kind is IRNodeKind.SECTION:
        anchor = node.attrs.get("_pdf_anchor")
        if anchor is not None:
            out.append((int(anchor[0]), float(anchor[1]), node.label or ""))
    for c in node.children:
        _collect_section_anchors(c, out)


def _bind_marginal_notes(
    body: IRNode,
    notes: Any,
    *,
    already_headed: bool = False,
) -> tuple[IRNode, list[Any]]:
    """Attach each marginal note to its vertically-nearest section as a heading.

    A note binds to the section whose opener sits on the same page at/above the
    note's ``y_top`` and is the nearest such opener within ``_NOTE_BIND_MAX_DY``.
    The bound note becomes a ``HEADING`` first-child on the section (matching the
    XML loader's ``uk_p1group_title_heading_carrier`` shape).  Notes that bind to
    no section are returned as the ``unbound`` list — a typed residual for
    genuine PDF lossiness (never silently dropped).

    Returns ``(new_body, unbound_notes)``.  The transient ``_pdf_anchor`` attr is
    stripped from the result regardless of binding outcome.
    """
    anchors: List[tuple[int, float, str]] = []
    _collect_section_anchors(body, anchors)

    # For each note, pick the section opener on the same page at/above the note,
    # nearest in y (smallest positive dy), within the bind window.
    bound: Dict[str, str] = {}
    unbound: list[Any] = []
    for note in notes:
        best_label: Optional[str] = None
        best_dy = _NOTE_BIND_MAX_DY
        for page, y, label in anchors:
            if page != note.page_num:
                continue
            dy = note.y_top - y
            if -8.0 <= dy <= best_dy and label not in bound:
                # note sits at/just-above/below the opener; nearest wins
                if abs(dy) < abs(best_dy) or best_label is None:
                    best_dy = dy
                    best_label = label
        if best_label is not None:
            # keep the first (topmost) note per section — the section's own
            # side-note is the one level with its opener.
            bound.setdefault(best_label, note.text)
        else:
            unbound.append(note)

    def rebuild(node: IRNode) -> IRNode:
        new_children = tuple(rebuild(c) for c in node.children)
        attrs = dict(node.attrs)
        attrs.pop("_pdf_anchor", None)
        if node.kind is IRNodeKind.SECTION and node.label in bound:
            has_heading = any(c.kind is IRNodeKind.HEADING for c in new_children)
            if not (already_headed or has_heading):
                heading = IRNode(
                    kind=IRNodeKind.HEADING,
                    text=bound[node.label],
                    attrs={"source_rule_id": "uk_pdf_marginal_note_heading_carrier"},
                )
                new_children = (heading, *new_children)
        return IRNode(kind=node.kind, label=node.label, text=node.text,
                      attrs=attrs, children=new_children)

    return rebuild(body), unbound


def uk_layout_to_ir(
    layout: Any, *, source_ref: str = ""
) -> tuple[IRNode, list[Any]]:
    """Parse a :class:`pdf_layout_uk.UKPdfLayout` into UK IR with bound side-notes.

    Runs the UK grammar over the marginal-free ``body_lines`` (side-note column
    already excised by the x-coordinate segmentation), threading each line's
    ``(page, y_top)`` anchor, then binds each recovered marginal note onto its
    section as a ``heading`` — the PDF analogue of the XML loader's
    ``P1group/Title`` heading carrier.

    Returns ``(body_ir, unbound_notes)``; ``unbound_notes`` is the typed residual
    of side-notes that bound to no section (genuine PDF lossiness).
    """
    positioned = list(getattr(layout, "positioned_body_lines", ()) or ())
    lines = [b.text for b in positioned]
    positions = [(b.page_num, b.y_top) for b in positioned]
    body = _text_lines_to_ir(lines, source_ref=source_ref, positions=positions)
    notes = list(getattr(layout, "marginal_notes", ()) or ())
    if not notes:
        return _strip_pdf_anchor(body), []
    return _bind_marginal_notes(body, notes)


def spine_summary(root: IRNode) -> dict[str, Any]:
    """Return a compact {sections, subsections, parts, schedules, items} count
    plus the ordered section labels — for prototype inspection / tests."""
    counts = {"part": 0, "section": 0, "subsection": 0, "item": 0, "schedule": 0, "paragraph": 0}
    section_labels: list[str] = []

    def walk(n: IRNode) -> None:
        k = n.kind
        if k is IRNodeKind.PART:
            counts["part"] += 1
        elif k is IRNodeKind.SECTION:
            counts["section"] += 1
            if n.label:
                section_labels.append(n.label)
        elif k is IRNodeKind.SUBSECTION:
            counts["subsection"] += 1
        elif k is IRNodeKind.ITEM:
            counts["item"] += 1
        elif k is IRNodeKind.SCHEDULE:
            counts["schedule"] += 1
        elif k is IRNodeKind.PARAGRAPH:
            counts["paragraph"] += 1
        for c in n.children:
            walk(c)

    walk(root)
    return {"counts": counts, "section_labels": section_labels}
