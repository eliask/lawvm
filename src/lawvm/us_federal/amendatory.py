"""Lower U.S. Public Law USLM amendatory text into canonical LegalOperation candidates.

This is the first U.S. surface that compiles enacted amendatory instructions into
core ``LegalOperation`` envelopes. It does **not** apply them, materialize text, or
claim replay agreement: every op produced here is a *candidate* whose truth is only
established later by the dry-run against the USC oracle.

Source signal
-------------
govinfo PLAW USLM XML marks amendatory language structurally:

- ``<ref href="/us/usc/t11/s101/10A">`` carries the amendment target (and its
  prose form ``Section 101(10A) of title 11, United States Code``);
- ``<amendingAction type="amend|delete|insert|add|redesignate|repeal">`` tags the
  action verbs;
- ``<quotedText>`` carries inline old/new strings (strike/insert);
- ``<quotedContent>`` carries quoted block payloads (add-at-end / amend-to-read).

We lower the *common* forms the prompt enumerates. Anything we cannot lower is
NEVER silently skipped: it becomes a typed finding (``us_amendatory_unlowered``)
and the instruction is recorded with status ``unsupported``/``needs_review``.

Prime Directive (AGENTS.md §0/§1): no silent target hijacking. Unresolved targets
and unparsable payloads are preserved as typed findings, not guessed away.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.parse_witness import ParseWitness
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction, TextPatchKindEnum

USLM_NS = "http://schemas.gpo.gov/xml/uslm"
_NS = {"u": USLM_NS}

# ---------------------------------------------------------------------------
# Witness rule ids (stable). Each lowered family carries its own id; the single
# finding id flags anything left unlowered.
# ---------------------------------------------------------------------------
RULE_STRIKE_INSERT = "us_amend_strike_insert"
RULE_STRIKE = "us_amend_strike"
RULE_INSERT_AFTER = "us_amend_insert_after_anchor"
RULE_ADD_AT_END = "us_amend_add_at_end"
RULE_AMEND_TO_READ = "us_amend_to_read"
RULE_REPEAL = "us_amend_repeal"
RULE_REDESIGNATE = "us_amend_redesignate"

UNLOWERED_FINDING_RULE_ID = "us_amendatory_unlowered"
TARGET_UNRESOLVED_FINDING_RULE_ID = "us_amendatory_target_unresolved"
NON_TITLE_TARGET_RULE_ID = "us_amendatory_target_non_us_code"

# USC nesting order (deepest-last). Used to type bare positional labels from a
# ref href / prose chain into the pinned LegalAddress segment kinds.
_USC_LEVELS = ("subsection", "paragraph", "subparagraph", "clause", "subclause", "item")


# ---------------------------------------------------------------------------
# Typed instruction + finding carriers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class USAmendatoryFinding:
    """Typed finding for an amendatory instruction we could not fully lower."""

    rule_id: str
    message: str
    statute_id: str
    instruction_id: str = ""
    target_phrase: str = ""
    target_href: str = ""
    raw_text: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "statute_id": self.statute_id,
            "instruction_id": self.instruction_id,
            "target_phrase": self.target_phrase,
            "target_href": self.target_href,
            "raw_text": self.raw_text,
        }


@dataclass(frozen=True)
class USAmendmentInstruction:
    """One lowered (or unlowered) amendatory instruction.

    ``status`` is ``accepted`` (op present and target resolved), ``unsupported``
    (form not lowerable; see ``finding``), or ``needs_review`` (lowered but the
    target or payload is partial / corroboration-only).
    """

    instruction_id: str
    status: str
    witness_rule_id: str
    action: str = ""
    target_phrase: str = ""
    target_href: str = ""
    target_address: LegalAddress | None = None
    operation: LegalOperation | None = None
    finding: USAmendatoryFinding | None = None
    parse_witness: ParseWitness | None = None
    raw_text: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "instruction_id": self.instruction_id,
            "status": self.status,
            "witness_rule_id": self.witness_rule_id,
            "action": self.action,
            "target_phrase": self.target_phrase,
            "target_href": self.target_href,
            "target_address": str(self.target_address) if self.target_address else "",
            "operation": _operation_jsonable(self.operation),
            "finding": self.finding.to_jsonable() if self.finding else None,
            "parse_witness_rule_id": self.parse_witness.rule_id if self.parse_witness else "",
            "raw_text": self.raw_text,
        }


@dataclass(frozen=True)
class USAmendatoryReport:
    """Lowered candidate ops + typed findings + witness-anchored coverage for one law."""

    statute_id: str
    enacted: str
    title_targets: tuple[str, ...]
    instructions: tuple[USAmendmentInstruction, ...]
    findings: tuple[USAmendatoryFinding, ...] = ()

    def operations(self) -> tuple[LegalOperation, ...]:
        return tuple(i.operation for i in self.instructions if i.operation is not None)

    def coverage(self) -> dict[str, Any]:
        total = len(self.instructions)
        lowered = sum(1 for i in self.instructions if i.operation is not None)
        accepted = sum(1 for i in self.instructions if i.status == "accepted")
        unsupported = sum(1 for i in self.instructions if i.status == "unsupported")
        needs_review = sum(1 for i in self.instructions if i.status == "needs_review")
        action_counts = Counter(i.action or "__none__" for i in self.instructions)
        witness_rule_counts = Counter(i.witness_rule_id for i in self.instructions)
        finding_rule_counts = Counter(f.rule_id for f in self.findings)
        return {
            "statute_id": self.statute_id,
            "enacted": self.enacted,
            "title_targets": sorted(self.title_targets),
            "instructions_total": total,
            "instructions_lowered": lowered,
            "instructions_accepted": accepted,
            "instructions_unsupported": unsupported,
            "instructions_needs_review": needs_review,
            "candidate_operations": lowered,
            "action_counts": dict(sorted(action_counts.items())),
            "witness_rule_counts": dict(sorted(witness_rule_counts.items())),
            "finding_rule_counts": dict(sorted(finding_rule_counts.items())),
            "findings_total": len(self.findings),
            "replay_claims": False,
            "candidate_claims": True,
        }

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "jurisdiction": "us_federal",
            "report_kind": "amendatory_candidates",
            "truth_claim": "candidate_legal_operations_not_replayed",
            "replay_claims": False,
            "candidate_claims": True,
            "coverage": self.coverage(),
            "instructions": [i.to_jsonable() for i in self.instructions],
            "findings": [f.to_jsonable() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Target address parsing (pinned USC LegalAddress convention)
# ---------------------------------------------------------------------------

# "Section 362(c)(1) of title 11, United States Code" / "section 1325(b)(4) of
# title 11". Labels are bare tokens; segments after the section are parenthesized.
_PROSE_TARGET_RE = re.compile(
    r"(?:^|\b)[Ss]ection\s+"
    r"(?P<section>\d+[A-Za-z]*(?:[-‐‑‒–]\d+)?)"
    r"(?P<segments>(?:\s*\([0-9A-Za-z]+\))*)"
    r"\s+of\s+title\s+(?P<title>\d+)",
)
_SEGMENT_RE = re.compile(r"\(([0-9A-Za-z]+)\)")
# ref href: /us/usc/t11/s101/10A  or  /us/usc/t11/s362/c/1
_HREF_TARGET_RE = re.compile(
    r"^/us/usc/t(?P<title>\d+)/s(?P<section>\d+[A-Za-z]*(?:[-‐‑‒–]\d+)?)"
    r"(?P<rest>(?:/[^/]+)*)$"
)


def _label_level(label: str, index: int) -> str:
    """Infer the USC segment kind for a positional label.

    USC labels are positional (subsection (a), paragraph (1), subparagraph (A),
    clause (i), subclause (I)). The label *form* disambiguates the common cases;
    we fall back to nesting depth when the form is unambiguous-by-position.
    """
    stripped = label.strip()
    if stripped[:1].isdigit():
        # Digit-led labels (incl. compound "10A") are paragraph-level in USC.
        kind = "paragraph"
    elif re.fullmatch(r"[ivxl]+", stripped):
        kind = "clause"
    elif re.fullmatch(r"[IVXL]+", stripped):
        kind = "subclause"
    elif stripped.islower():
        kind = "subsection"
    elif stripped.isupper():
        kind = "subparagraph"
    else:
        kind = "subsection"
    # Keep the inferred form but never let it collide with a coarser depth than
    # its position permits: deeper index can only refine, not jump back.
    floor = _USC_LEVELS[min(index, len(_USC_LEVELS) - 1)]
    if _USC_LEVELS.index(kind) < _USC_LEVELS.index(floor):
        return floor
    return kind


def parse_usc_target_phrase(phrase: str) -> LegalAddress | None:
    """Parse a prose amendment target phrase into the pinned USC LegalAddress.

    Returns ``None`` when the phrase is not a "Section X(...) of title N" form.
    """
    match = _PROSE_TARGET_RE.search(phrase)
    if match is None:
        return None
    title = match.group("title")
    section = match.group("section")
    path: list[tuple[str, str]] = [("title", title), ("section", section)]
    for i, seg in enumerate(_SEGMENT_RE.findall(match.group("segments") or "")):
        path.append((_label_level(seg, i), seg))
    return LegalAddress(path=tuple(path))


def parse_usc_target_href(href: str) -> LegalAddress | None:
    """Parse a ``/us/usc/t{N}/s{section}/...`` ref href into a USC LegalAddress.

    Trailing ``/note``, ``/etseq`` and similar non-structural carriers are dropped
    (they are citation facets, not addressable sub-structure).
    """
    match = _HREF_TARGET_RE.match(href.strip())
    if match is None:
        return None
    path: list[tuple[str, str]] = [
        ("title", match.group("title")),
        ("section", match.group("section")),
    ]
    rest = match.group("rest") or ""
    idx = 0
    for seg in (s for s in rest.split("/") if s):
        if seg in ("note", "etseq", "et_seq"):
            continue
        path.append((_label_level(seg, idx), seg))
        idx += 1
    return LegalAddress(path=tuple(path))


# ---------------------------------------------------------------------------
# Lowering an instruction section
# ---------------------------------------------------------------------------


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_of(elem: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(elem.itertext())).strip()


def _quoted_texts(elem: ET.Element) -> list[str]:
    out: list[str] = []
    for q in elem.iter():
        if _localname(q.tag) == "quotedText":
            out.append("".join(q.itertext()).strip())
    return out


def _amending_actions(elem: ET.Element) -> list[str]:
    out: list[str] = []
    for a in elem.iter():
        if _localname(a.tag) == "amendingAction":
            out.append((a.get("type") or "").strip())
    return out


def _quoted_content_node(elem: ET.Element) -> IRNode | None:
    """Build an IRNode payload from the first ``<quotedContent>`` block, if any."""
    for q in elem.iter():
        if _localname(q.tag) == "quotedContent":
            text = re.sub(r"\s+", " ", "".join(q.itertext())).strip().strip("“”\".")
            # We carry the quoted block verbatim as a single content node; the
            # dry-run stage re-parses the USLM sub-tree into structured law.
            return IRNode(kind=IRNodeKind.CONTENT, text=text)
    return None


def _resolve_target(
    target_phrase: str,
    target_href: str,
) -> tuple[LegalAddress | None, str]:
    """Resolve the instruction target; prose is canonical, href corroborates.

    Returns ``(address, resolution_status)`` where status is one of
    ``prose``, ``href``, ``prose_href_agree``, or ``unresolved``.
    """
    prose_addr = parse_usc_target_phrase(target_phrase) if target_phrase else None
    href_addr = parse_usc_target_href(target_href) if target_href else None
    if prose_addr is not None and href_addr is not None:
        if prose_addr.path == href_addr.path:
            return prose_addr, "prose_href_agree"
        # Prose is canonical (the enacted language); href is a converter artifact.
        return prose_addr, "prose"
    if prose_addr is not None:
        return prose_addr, "prose"
    if href_addr is not None:
        return href_addr, "href"
    return None, "unresolved"


def _classify_action(actions: list[str], raw_text: str) -> str:
    """Map the amendingAction verb sequence / prose to a canonical family token."""
    has = set(actions)
    lowered = raw_text.lower()
    if "repeal" in has or re.search(r"\bis repealed\b", lowered):
        return "repeal"
    if "redesignate" in has or "redesignat" in lowered:
        return "redesignate"
    if ("amend" in has and "to read" in lowered) or "to read as follows" in lowered:
        return "amend_to_read"
    if ("delete" in has and ("insert" in has or "add" in has)) or (
        "striking" in lowered and ("inserting" in lowered)
    ):
        return "strike_insert"
    if "add" in has and "at the end" in lowered:
        return "add_at_end"
    if "delete" in has or "striking" in lowered:
        return "strike"
    if "insert" in has and " after " in lowered:
        return "insert_after"
    if "add" in has or "insert" in has:
        return "add_at_end"
    return "unknown"


def _redesignate_destination(
    raw_text: str, target: LegalAddress
) -> tuple[LegalAddress, LegalAddress] | None:
    """Parse ``redesignating X as Y`` into ``(from, to)`` addresses (single-unit form)."""
    m = re.search(
        r"redesignating\s+(?:subsection|paragraph|subparagraph|clause|subclause)\s+"
        r"\(([0-9A-Za-z]+)\)\s+as\s+"
        r"(?:subsection|paragraph|subparagraph|clause|subclause)\s+\(([0-9A-Za-z]+)\)",
        raw_text,
        re.IGNORECASE,
    )
    if m is None:
        return None
    from_label, to_label = m.group(1), m.group(2)
    parent = target  # target already resolves to the enclosing section/subsection
    leaf_index = max(parent.depth() - 2, 0)
    from_kind = _label_level(from_label, leaf_index)
    from_addr = LegalAddress(path=(*parent.path, (from_kind, from_label)))
    to_addr = LegalAddress(path=(*parent.path, (from_kind, to_label)))
    return from_addr, to_addr


def _lower_instruction(
    *,
    statute_id: str,
    enacted: str,
    instruction_id: str,
    sequence: int,
    target_phrase: str,
    target_href: str,
    raw_text: str,
    quoted: list[str],
    actions: list[str],
    payload_node: IRNode | None,
) -> USAmendmentInstruction:
    source = OperationSource(statute_id=statute_id, enacted=enacted, raw_text=raw_text)
    address, resolution_status = _resolve_target(target_phrase, target_href)
    family = _classify_action(actions, raw_text)

    def _finding(rule_id: str, message: str) -> USAmendatoryFinding:
        return USAmendatoryFinding(
            rule_id=rule_id,
            message=message,
            statute_id=statute_id,
            instruction_id=instruction_id,
            target_phrase=target_phrase,
            target_href=target_href,
            raw_text=raw_text,
        )

    # Target gate: never hijack. Unresolved target → unsupported finding.
    if address is None:
        finding = _finding(
            TARGET_UNRESOLVED_FINDING_RULE_ID,
            f"could not resolve amendment target for {family!r} instruction "
            f"(phrase={target_phrase!r}, href={target_href!r})",
        )
        return USAmendmentInstruction(
            instruction_id=instruction_id,
            status="unsupported",
            witness_rule_id=UNLOWERED_FINDING_RULE_ID,
            action=family,
            target_phrase=target_phrase,
            target_href=target_href,
            finding=finding,
            parse_witness=ParseWitness(rule_id=UNLOWERED_FINDING_RULE_ID),
            raw_text=raw_text,
        )

    # Off-Title-11 targets are resolvable but out of this surface's scope; record
    # them as needs_review rather than emit a candidate into the wrong corpus.
    if address.path and address.path[0] == ("title", "11"):
        on_title_11 = True
    else:
        on_title_11 = False

    op: LegalOperation | None = None
    witness_rule_id = UNLOWERED_FINDING_RULE_ID
    status = "unsupported"
    finding: USAmendatoryFinding | None = None

    def _make_op(
        action: StructuralAction,
        *,
        rule_id: str,
        payload: IRNode | None = None,
        anchor: LegalAddress | None = None,
        destination: LegalAddress | None = None,
        text_patch: TextPatchSpec | None = None,
    ) -> LegalOperation:
        return LegalOperation(
            op_id=instruction_id,
            sequence=sequence,
            action=action,
            target=address,
            payload=payload,
            anchor=anchor,
            destination=destination,
            source=source,
            text_patch=text_patch,
            witness_rule_id=rule_id,
            provenance_tags=("us_amendatory", f"target_resolution:{resolution_status}"),
        )

    if family == "strike_insert":
        if len(quoted) >= 2:
            old, new = quoted[0], quoted[1]
            op = _make_op(
                StructuralAction.TEXT_REPLACE,
                rule_id=RULE_STRIKE_INSERT,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(
                        match_text=old,
                        occurrence=-1 if "each place" in raw_text.lower() else 0,
                    ),
                    replacement=new,
                ),
            )
            witness_rule_id = RULE_STRIKE_INSERT
        elif payload_node is not None and quoted:
            # strike <label> and insert <block> -> whole-node REPLACE of the struck unit.
            op = _make_op(
                StructuralAction.REPLACE,
                rule_id=RULE_STRIKE_INSERT,
                payload=payload_node,
            )
            witness_rule_id = RULE_STRIKE_INSERT
        elif payload_node is not None:
            op = _make_op(
                StructuralAction.REPLACE, rule_id=RULE_STRIKE_INSERT, payload=payload_node
            )
            witness_rule_id = RULE_STRIKE_INSERT
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "strike-and-insert without two quoted strings or a quoted block payload",
            )
    elif family == "strike":
        if quoted:
            op = _make_op(
                StructuralAction.TEXT_REPEAL,
                rule_id=RULE_STRIKE,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.DELETE,
                    selector=TextSelector(
                        match_text=quoted[0],
                        occurrence=-1 if "each place" in raw_text.lower() else 0,
                    ),
                ),
            )
            witness_rule_id = RULE_STRIKE
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "strike with no quoted string (structural-unit strike not yet lowered)",
            )
    elif family == "insert_after":
        if len(quoted) >= 2:
            new_text, anchor_text = quoted[0], quoted[1]
            op = _make_op(
                StructuralAction.TEXT_REPLACE,
                rule_id=RULE_INSERT_AFTER,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(match_text=anchor_text),
                    replacement=anchor_text + new_text,
                ),
            )
            witness_rule_id = RULE_INSERT_AFTER
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "insert-after without both inserted text and anchor text",
            )
    elif family == "add_at_end":
        if payload_node is not None:
            op = _make_op(
                StructuralAction.INSERT,
                rule_id=RULE_ADD_AT_END,
                payload=payload_node,
                anchor=address,
            )
            witness_rule_id = RULE_ADD_AT_END
        elif quoted:
            op = _make_op(
                StructuralAction.INSERT,
                rule_id=RULE_ADD_AT_END,
                payload=IRNode(kind=IRNodeKind.CONTENT, text=quoted[0]),
                anchor=address,
            )
            witness_rule_id = RULE_ADD_AT_END
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID, "add-at-end without a quoted payload"
            )
    elif family == "amend_to_read":
        if payload_node is not None:
            op = _make_op(
                StructuralAction.REPLACE, rule_id=RULE_AMEND_TO_READ, payload=payload_node
            )
            witness_rule_id = RULE_AMEND_TO_READ
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID, "amend-to-read without a quoted replacement block"
            )
    elif family == "repeal":
        op = _make_op(StructuralAction.REPEAL, rule_id=RULE_REPEAL)
        witness_rule_id = RULE_REPEAL
    elif family == "redesignate":
        pair = _redesignate_destination(raw_text, address)
        if pair is not None:
            from_addr, to_addr = pair
            op = LegalOperation(
                op_id=instruction_id,
                sequence=sequence,
                action=StructuralAction.RENUMBER,
                target=from_addr,
                destination=to_addr,
                source=source,
                witness_rule_id=RULE_REDESIGNATE,
                provenance_tags=("us_amendatory", f"target_resolution:{resolution_status}"),
            )
            witness_rule_id = RULE_REDESIGNATE
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "redesignation is multi-unit or range form (not yet lowered to a single RENUMBER)",
            )
    else:
        finding = _finding(
            UNLOWERED_FINDING_RULE_ID,
            f"amendatory form not recognized (actions={actions!r})",
        )

    if op is not None:
        status = "accepted" if on_title_11 else "needs_review"
        if not on_title_11:
            finding = _finding(
                NON_TITLE_TARGET_RULE_ID,
                f"resolved target is outside Title 11 ({address.path[0] if address.path else ()}); "
                "candidate withheld from Title 11 scope",
            )

    return USAmendmentInstruction(
        instruction_id=instruction_id,
        status=status,
        witness_rule_id=witness_rule_id,
        action=family,
        target_phrase=target_phrase,
        target_href=target_href,
        target_address=address,
        operation=op,
        finding=finding,
        parse_witness=ParseWitness(rule_id=witness_rule_id),
        raw_text=raw_text,
    )


# ---------------------------------------------------------------------------
# Instruction extraction from a USLM section
# ---------------------------------------------------------------------------


def _first_usc_ref(content: ET.Element) -> tuple[str, str]:
    """Return ``(prose_phrase, href)`` for the first USC structural ref in content."""
    for ref in content.iter():
        if _localname(ref.tag) != "ref":
            continue
        href = ref.get("href", "")
        if "/usc/" not in href:
            continue
        phrase = "".join(ref.itertext()).strip()
        # Skip pure "note" citations (editorial cross-refs), not amendment targets.
        if phrase.lower().endswith("note"):
            continue
        return phrase, href
    return "", ""


def _iter_instruction_units(
    section: ET.Element,
) -> Iterable[tuple[str, ET.Element]]:
    """Yield ``(unit_id, element)`` for each amendatory unit inside a section.

    A unit is either the section's own direct ``<content>`` (flat instruction) or
    each nested ``<paragraph>/<subparagraph>`` that carries its own amendingAction
    ("(1) in subsection (b)— (A) by striking…"). We carry the enclosing section
    target into sub-units that lack their own ref.
    """
    nested = [
        elem
        for elem in section.iter()
        if _localname(elem.tag) in ("paragraph", "subparagraph", "clause")
        and any(_localname(a.tag) == "amendingAction" for a in elem.iter())
        # only leaf-ish units: a unit whose own descendants do not themselves carry
        # a deeper amendingAction-bearing paragraph
    ]
    leaf_units = []
    for elem in nested:
        has_deeper = any(
            child is not elem
            and _localname(child.tag) in ("paragraph", "subparagraph", "clause")
            and any(_localname(a.tag) == "amendingAction" for a in child.iter())
            for child in elem.iter()
        )
        if not has_deeper:
            leaf_units.append(elem)

    if leaf_units:
        for elem in leaf_units:
            uid = elem.get("identifier") or elem.get("id") or ""
            yield uid, elem
        return
    # Flat instruction: the section's own content blocks.
    yield (section.get("identifier") or section.get("id") or ""), section


def lower_plaw_amendatory(data: bytes, *, statute_id: str = "", enacted: str = "") -> USAmendatoryReport:
    """Lower one Public Law's USLM amendatory text into candidate operations."""
    root = ET.fromstring(data)
    congress = (root.findtext(".//u:meta/u:congress", namespaces=_NS) or "").strip()
    docnum = (root.findtext(".//u:meta/u:docNumber", namespaces=_NS) or "").strip()
    approved = (root.findtext(".//u:meta/u:approvedDate", namespaces=_NS) or "").strip()
    if not statute_id:
        statute_id = f"PL {congress}-{docnum}" if congress and docnum else "PL ?-?"
    if not enacted:
        enacted = approved

    title_targets: set[str] = set()
    instructions: list[USAmendmentInstruction] = []
    findings: list[USAmendatoryFinding] = []
    sequence = 0

    main = root.find(".//u:main", _NS)
    if main is None:
        return USAmendatoryReport(statute_id=statute_id, enacted=enacted, title_targets=(), instructions=())

    for section in main.iter():
        if _localname(section.tag) != "section":
            continue
        # Section-level target ref (carried into sub-units without their own ref).
        section_content = section.find("u:content", _NS)
        sec_phrase, sec_href = ("", "")
        if section_content is not None:
            sec_phrase, sec_href = _first_usc_ref(section_content)
        # Skip pure short-title / non-amendatory sections.
        if not any(_localname(a.tag) == "amendingAction" for a in section.iter()):
            continue

        for unit_id, unit in _iter_instruction_units(section):
            actions = _amending_actions(unit)
            if not actions:
                continue
            unit_phrase, unit_href = _first_usc_ref(unit)
            target_phrase = unit_phrase or sec_phrase
            target_href = unit_href or sec_href
            raw_text = _text_of(unit)
            quoted = _quoted_texts(unit)
            payload_node = _quoted_content_node(unit)
            sequence += 1
            instr = _lower_instruction(
                statute_id=statute_id,
                enacted=enacted,
                instruction_id=unit_id or f"{statute_id}#instr{sequence}",
                sequence=sequence,
                target_phrase=target_phrase,
                target_href=target_href,
                raw_text=raw_text,
                quoted=quoted,
                actions=actions,
                payload_node=payload_node,
            )
            instructions.append(instr)
            if instr.finding is not None:
                findings.append(instr.finding)
            if instr.target_address is not None and instr.target_address.path:
                title_targets.add(f"title {instr.target_address.path[0][1]}")

    return USAmendatoryReport(
        statute_id=statute_id,
        enacted=enacted,
        title_targets=tuple(sorted(title_targets)),
        instructions=tuple(instructions),
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# JSON projection helpers
# ---------------------------------------------------------------------------


def _operation_jsonable(op: LegalOperation | None) -> dict[str, Any] | None:
    if op is None:
        return None
    patch: dict[str, Any] | None = None
    if op.text_patch is not None:
        patch = {
            "kind": op.text_patch.kind.value,
            "match_text": op.text_patch.selector.match_text,
            "occurrence": op.text_patch.selector.occurrence,
            "replacement": op.text_patch.replacement,
        }
    return {
        "op_id": op.op_id,
        "sequence": op.sequence,
        "action": str(op.action),
        "target": str(op.target),
        "destination": str(op.destination) if op.destination else "",
        "anchor": str(op.anchor) if op.anchor else "",
        "witness_rule_id": op.witness_rule_id,
        "text_patch": patch,
        "payload_text": op.payload.text if op.payload is not None else "",
        "provenance_tags": list(op.provenance_tags),
        "statute_id": op.source.statute_id if op.source else "",
        "enacted": op.source.enacted if op.source else "",
    }
