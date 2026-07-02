"""Typed source-tree extraction for New Zealand legislation XML.

This is a source parsing layer, not replay. It preserves XML ids, labels,
headings, text, deletion status, and amendment-history witnesses so later NZ
replay work can lower from explicit source facts instead of scraping strings
from final text.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

from lxml import etree

from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dates import nz_date_text_to_iso
from lawvm.new_zealand.dependencies import latest_xml_locator_for_work, parse_public_act_citation


_STRUCTURAL_TAGS = {"def-para", "label-para", "part", "prov", "schedule", "subprov"}
# Subtrees that contribute NO legal flow text. ``notes``/``history`` carry
# amendment-history annotations; ``summary`` is the auto-generated accessibility
# caption of a ``<legtable>`` ("The following table is small in size and has N
# columns…") — screen-reader metadata, never operative legal content. The PCO
# consolidation does not carry the caption into the operative text at the
# amendment's snapshot, so leaving it in our extracted payload makes the amend
# payload diverge spuriously from the oracle. Excluding it folds both sides
# symmetrically (the caption is dropped from candidate AND oracle extraction).
#
# ``graphic``/``eqn-line`` are the FORMULA rendering of an ``<eqn>`` math block.
# The PCO consolidation renders a formula as a ``<graphic>`` SVG image (no text),
# while an amending act's payload may carry the SAME formula as a run of
# ``<eqn-line>`` text fragments inside a layout table ("{[(1 + P1) × (1 + P2)]
# − 1} × 100"). Comparing image-vs-text for one math block is a spurious diff,
# not a content divergence — the canonical text-state legitimately omits the
# inline formula (it lives as a graphic reference). The surrounding ``<eqn>``
# prose ("where—") and ``<variable-def>`` blocks are NOT formula lines, so they
# are retained on both sides. Excluding the formula rendering folds the math
# block symmetrically.
#
# ``cf`` is the "Compare:" source-origin footnote (e.g. ``<cf><citation>2008 No 72
# s 79A</citation></cf>`` appended to an inserted provision) — editorial provenance
# metadata, not legislative content. Like history/notes it must be excluded from
# legal text, or its trailing "YYYY No N s X" annotation leaks into a provision's
# text and shows up as a spurious substantive divergence against an amending
# payload that (correctly) carries no such footnote.
_TEXT_EXCLUDE_TAGS = {"notes", "history", "history-note", "summary", "graphic", "eqn-line", "cf"}

# A ``def-para`` (a single definition in an interpretation/definitions
# provision) is addressed by its defined term rather than a numeric label. NZ
# wraps the defined term in a ``def-term`` child of the leading ``text``; we use
# the first such term, normalized, as the addressable label.
_DEF_TERM_TAG = "def-term"


@dataclass(frozen=True)
class NZAmendInstruction:
    """One typed amending instruction read from an amending act's ``<text>``.

    NZ amending provisions carry the old/new payload in paired ``<amend.in>``
    elements and the target in a ``<citation>`` (``<extref>`` for modern acts,
    an ``<atidlm:linkcontent>`` for older consolidated acts). A single
    ``<text>`` element is one instruction; a multi-instruction provision has
    several sibling ``<text>`` elements under one ``amending-provision`` href.

    This is the structured alternative to scraping the flattened node prose: by
    reading the typed elements directly we recover multi-instruction provisions
    (one href, N instructions) and exact old/new text instead of regex guesses.
    ``verb`` is the prose operation word (``omitting_substituting``,
    ``inserting``, ``omitting`` …) classified from the surrounding ``<text>``;
    only ``omitting_substituting`` with an exact old/new pair is currently a
    single-occurrence text-replace candidate — everything else stays a typed
    not-yet-supported instruction, never a guess.
    """

    target_citation: str
    verb: str
    old_text: str
    new_text: str
    each_place: bool
    # Structural-amend payloads beyond the omit/substitute pair. ``anchor_text``
    # is the existing text an ``inserting`` instruction inserts relative to (the
    # ``<quote.in>`` reference word); ``insert_position`` is ``"after"`` or
    # ``"before"``. Both stay empty for the substitution verbs. ``omit_only`` is
    # ``True`` for an ``omitting`` instruction that deletes a single ``<amend.in>``
    # span with no replacement (lowered as a text-replace to the empty string).
    anchor_text: str = ""
    insert_position: str = ""
    omit_only: bool = False

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "target_citation": self.target_citation,
            "verb": self.verb,
            "old_text": self.old_text,
            "new_text": self.new_text,
            "each_place": self.each_place,
            "anchor_text": self.anchor_text,
            "insert_position": self.insert_position,
            "omit_only": self.omit_only,
        }


@dataclass(frozen=True)
class NZHistoryWitness:
    xml_id: str
    xml_path: str
    text: str
    amended_provision: str
    operation: str
    amendment_date: str
    amendment_date_iso: str
    amending_provisions: tuple[str, ...]
    amending_provision_hrefs: tuple[str, ...]
    amending_legislation: str
    amending_work_id: str
    # Defined term targeted by a definition-level note ("Section 2(1) <term>:
    # repealed, ..."), taken from the bold ``emphasis`` between the
    # ``amended-provision`` reference and the operation. Empty for ordinary
    # (non-definition) targets.
    defined_term: str = ""
    # Stable rule_id of any legacy history-note recovery that sourced this
    # witness's operation verb (empty when the verb came from the canonical
    # ``<amending-operation>`` element). Emitted downstream as an ag(
    # non-blocking evidence finding per AGENTS §2.1 (a heuristic that
    # affects op-family classification needs: stable rule_id, family tag,
    # source witness, finding emission, strict-mode behavior, synthetic
    # test, real-corpus regression).
    recovery_rule_id: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "xml_id": self.xml_id,
            "xml_path": self.xml_path,
            "text": self.text,
            "amended_provision": self.amended_provision,
            "operation": self.operation,
            "amendment_date": self.amendment_date,
            "amendment_date_iso": self.amendment_date_iso,
            "amending_provisions": list(self.amending_provisions),
            "amending_provision_hrefs": list(self.amending_provision_hrefs),
            "amending_legislation": self.amending_legislation,
            "amending_work_id": self.amending_work_id,
            "defined_term": self.defined_term,
            "recovery_rule_id": self.recovery_rule_id,
        }


@dataclass(frozen=True)
class NZSourceNode:
    kind: str
    path: tuple[str, ...]
    xml_id: str
    xml_path: str
    source_zone: str
    label: str
    heading: str
    deletion_status: str
    text: str
    history: tuple[NZHistoryWitness, ...]
    # Typed amending instructions read from this node's ``<amend.in>``/citation
    # payload when the node is an amending-act provision. Empty for ordinary
    # (non-amending) nodes and for amending nodes whose payload is not typed
    # (e.g. schedule indirection, structural ``<amend>`` subtrees).
    amend_instructions: tuple[NZAmendInstruction, ...] = ()

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": list(self.path),
            "xml_id": self.xml_id,
            "xml_path": self.xml_path,
            "source_zone": self.source_zone,
            "label": self.label,
            "heading": self.heading,
            "deletion_status": self.deletion_status,
            "text": self.text,
            "history": [row.to_jsonable() for row in self.history],
            "amend_instructions": [row.to_jsonable() for row in self.amend_instructions],
        }


@dataclass(frozen=True)
class NZSourceDocument:
    xml_locator: str
    version_id: str
    metadata: Mapping[str, str]
    nodes: tuple[NZSourceNode, ...]
    document_history: tuple[NZHistoryWitness, ...]

    def summary(self) -> dict[str, Any]:
        kinds = Counter(node.kind for node in self.nodes)
        deleted = sum(1 for node in self.nodes if node.deletion_status)
        history_count = sum(len(node.history) for node in self.nodes) + len(self.document_history)
        amending_work_count = len(
            {
                witness.amending_work_id
                for node in self.nodes
                for witness in node.history
                if witness.amending_work_id
            }
            | {witness.amending_work_id for witness in self.document_history if witness.amending_work_id}
        )
        return {
            "xml_locator": self.xml_locator,
            "version_id": self.version_id,
            "title": self.metadata.get("title", ""),
            "as_at": self.metadata.get("date.as.at", ""),
            "assent": self.metadata.get("date.assent", ""),
            "nodes": len(self.nodes),
            "node_kinds": dict(sorted(kinds.items())),
            "deleted_nodes": deleted,
            "history_witnesses": history_count,
            "amending_works": amending_work_count,
        }

    def to_jsonable(self, *, include_nodes: bool = True) -> dict[str, Any]:
        payload = {
            "xml_locator": self.xml_locator,
            "version_id": self.version_id,
            "metadata": dict(self.metadata),
            "summary": self.summary(),
            "document_history": [row.to_jsonable() for row in self.document_history],
        }
        if include_nodes:
            payload["nodes"] = [node.to_jsonable() for node in self.nodes]
        return payload


def parse_nz_source_document(
    xml_bytes: bytes,
    *,
    xml_locator: str = "",
    version_id: str = "",
) -> NZSourceDocument:
    # Inside a corpus/north-star run, memoize parsed documents by their
    # (xml_locator, version_id) identity so the same archived version XML is parsed
    # at most once across families/works. The parse is pure and the result frozen,
    # so a cache hit is byte-identical to a fresh parse. Outside a run this is a
    # plain parse.
    from lawvm.new_zealand.corpus_cache import active_corpus_run_cache

    cache = active_corpus_run_cache()
    if cache is not None:
        return cache.parse_document(
            xml_bytes,
            xml_locator=xml_locator,
            version_id=version_id,
            parser=_parse_nz_source_document_uncached,
        )
    return _parse_nz_source_document_uncached(xml_bytes, xml_locator=xml_locator, version_id=version_id)


def _parse_nz_source_document_uncached(
    xml_bytes: bytes,
    *,
    xml_locator: str = "",
    version_id: str = "",
) -> NZSourceDocument:
    root = parse_corpus_xml(xml_bytes)
    metadata = _document_metadata(root)
    nodes: list[NZSourceNode] = []
    document_history: list[NZHistoryWitness] = []
    attached_history_note_keys: set[str] = set()
    legal_text_cache: dict[tuple[etree._Element, bool], str] = {}
    amend_instruction_text_nodes_by_candidate = (
        _amend_instruction_candidate_nodes(root)
        if b"amend.in" in xml_bytes
        else {}
    )

    for child in root:
        _walk_source_nodes(
            child,
            path=(),
            nodes=nodes,
            attached_history_note_keys=attached_history_note_keys,
            legal_text_cache=legal_text_cache,
            amend_instruction_text_nodes_by_candidate=amend_instruction_text_nodes_by_candidate,
        )

    for note in _iter_localname(root, "history-note"):
        if _element_source_key(note) not in attached_history_note_keys:
            document_history.append(_history_witness(note))

    return NZSourceDocument(
        xml_locator=xml_locator,
        version_id=version_id,
        metadata=metadata,
        nodes=tuple(nodes),
        document_history=tuple(document_history),
    )


def parse_archived_work_latest(db_path: Path, work_id: str) -> NZSourceDocument:
    archive = open_farchive(db_path)
    try:
        version_id, xml_locator = latest_xml_locator_for_work(archive, work_id)
        if not xml_locator:
            raise RuntimeError(f"no archived latest XML for {work_id}")
        data = archive.get(xml_locator)
    finally:
        archive.close()
    if data is None:
        raise RuntimeError(f"archived XML locator unreadable: {xml_locator}")
    return parse_nz_source_document(data, xml_locator=xml_locator, version_id=version_id)


def _walk_source_nodes(
    node: etree._Element,
    *,
    path: tuple[str, ...],
    nodes: list[NZSourceNode],
    attached_history_note_keys: set[str],
    legal_text_cache: dict[tuple[etree._Element, bool], str],
    amend_instruction_text_nodes_by_candidate: Mapping[etree._Element, tuple[etree._Element, ...]],
) -> None:
    if not isinstance(node.tag, str):
        return
    # ``_localname`` is the #1 chain-replay hotspot (~25M calls); here and at
    # the other call sites below we inline ``_localname_of_tag(node.tag)``
    # because ``isinstance(node.tag, str)`` is already verified. This saves
    # the ``_localname`` Python frame, the ``hasattr`` precheck, and the
    # ``isinstance(value, str)`` branch on ~2M calls, keeping the lru_cached
    # tag-split (which dominates for ~30 unique NZ tag names).
    kind = _localname_of_tag(node.tag)
    if kind in _TEXT_EXCLUDE_TAGS:
        return
    if kind in _STRUCTURAL_TAGS:
        if kind == "def-para":
            label = _first_def_term(node)
        else:
            label = _direct_child_text(node, "label")
        segment = _path_segment(kind, label, _attr(node, "id"), len(nodes) + 1)
        current_path = (*path, segment)
        history_notes = tuple(_direct_history_notes(node))
        attached_history_note_keys.update(_element_source_key(note) for note in history_notes)
        xml_path = _element_source_key(node)
        source_node = NZSourceNode(
            kind=kind,
            path=current_path,
            xml_id=_attr(node, "id"),
            xml_path=xml_path,
            source_zone=_source_zone(xml_path),
            label=label,
            heading=_direct_child_text(node, "heading"),
            deletion_status=_attr(node, "deletion-status"),
            text=_legal_text(node, cache=legal_text_cache),
            history=tuple(_history_witness(note) for note in history_notes),
            amend_instructions=(
                _amend_instructions_from_text_nodes(text_nodes)
                if (text_nodes := amend_instruction_text_nodes_by_candidate.get(node))
                else ()
            ),
        )
        nodes.append(source_node)
        for child in node:
            _walk_source_nodes(
                child,
                path=current_path,
                nodes=nodes,
                attached_history_note_keys=attached_history_note_keys,
                legal_text_cache=legal_text_cache,
                amend_instruction_text_nodes_by_candidate=amend_instruction_text_nodes_by_candidate,
            )
        return
    for child in node:
        _walk_source_nodes(
            child,
            path=path,
            nodes=nodes,
            attached_history_note_keys=attached_history_note_keys,
            legal_text_cache=legal_text_cache,
            amend_instruction_text_nodes_by_candidate=amend_instruction_text_nodes_by_candidate,
        )


def _amend_instruction_candidate_nodes(root: etree._Element) -> dict[etree._Element, tuple[etree._Element, ...]]:
    """Map elements to descendant ``<text>`` nodes containing ``<amend.in>``.

    The amendment-instruction parser remains the semantic authority. This map is
    only a per-parse negative prefilter and text-node index for the common
    consolidated-source case: if no ``<amend.in>`` exists below an element,
    ``_amend_instructions`` would necessarily return ``()`` after walking that
    whole subtree.
    """

    candidate_text_nodes: dict[etree._Element, list[etree._Element]] = {}
    candidate_seen_text_nodes: dict[etree._Element, set[etree._Element]] = {}
    for amend_in in root.iter():
        if not isinstance(amend_in.tag, str) or _localname_of_tag(amend_in.tag) != "amend.in":
            continue
        text_node = _nearest_ancestor_localname(amend_in, "text")
        if text_node is None:
            continue
        cursor: etree._Element | None = text_node
        while cursor is not None:
            seen = candidate_seen_text_nodes.setdefault(cursor, set())
            if text_node not in seen:
                seen.add(text_node)
                candidate_text_nodes.setdefault(cursor, []).append(text_node)
            if cursor is root:
                break
            cursor = cursor.getparent()
    return {node: tuple(text_nodes) for node, text_nodes in candidate_text_nodes.items()}


def _nearest_ancestor_localname(node: etree._Element, localname: str) -> etree._Element | None:
    cursor: etree._Element | None = node
    while cursor is not None:
        if isinstance(cursor.tag, str) and _localname_of_tag(cursor.tag) == localname:
            return cursor
        cursor = cursor.getparent()
    return None


def _document_metadata(root: etree._Element) -> dict[str, str]:
    metadata: dict[str, str] = {
        _localname_of_tag(key): value
        for key, value in root.attrib.items()
    }
    title = ""
    for node in root.iter():
        if isinstance(node.tag, str) and _localname_of_tag(node.tag) == "title":
            title = _node_text(node)
            break
    if title:
        metadata["title"] = title
    metadata["root_tag"] = _localname_of_tag(root.tag) if isinstance(root.tag, str) else _localname(root)
    return metadata


def _first_def_term(node: etree._Element) -> str:
    """Return the first defined term inside a ``def-para`` as an addressable label.

    The label is the normalized text of the first ``def-term`` descendant. Path
    segments use ``/`` and ``:`` as separators, so a term carrying either is not
    a clean addressable label; we drop it (the segment then falls back to the
    XML id) rather than corrupt the path.
    """
    for descendant in node.iter():
        if descendant is node:
            continue
        if isinstance(descendant.tag, str) and _localname_of_tag(descendant.tag) == _DEF_TERM_TAG:
            term = _normalize_text(descendant.text or "")
            if term and "/" not in term and ":" not in term:
                return term
            return ""
    return ""


def _path_segment(kind: str, label: str, xml_id: str, ordinal: int) -> str:
    if label:
        return f"{kind}:{label}"
    if xml_id:
        return f"{kind}@{xml_id}"
    return f"{kind}#{ordinal}"


def _direct_history_notes(node: etree._Element) -> Iterable[etree._Element]:
    for child in node:
        if not isinstance(child.tag, str):
            continue
        child_kind = _localname_of_tag(child.tag)
        if child_kind == "notes":
            for descendant in child.iter():
                if (
                    isinstance(descendant.tag, str)
                    and _localname_of_tag(descendant.tag) == "history-note"
                ):
                    yield descendant
        elif child_kind == "history-note":
            yield child


def _iter_localname(root: etree._Element, localname: str) -> Iterable[etree._Element]:
    for node in root.iter():
        if isinstance(node.tag, str) and _localname_of_tag(node.tag) == localname:
            yield node


def _element_source_key(node: etree._Element) -> str:
    return node.getroottree().getpath(node)


def _history_witness(node: etree._Element) -> NZHistoryWitness:
    text = _node_text(node)
    parsed = parse_public_act_citation(text)
    work_id = ""
    if parsed is not None:
        _title, year, number = parsed
        work_id = f"act_public_{year}_{number}"
    amendment_date = _first_descendant_text(node, "amendment-date")
    amended_provision = _first_descendant_text(node, "amended-provision")
    operation = _first_descendant_text(node, "amending-operation")
    recovery_rule_id = ""
    # Legacy recovery: when the canonical ``<amending-operation>`` element is
    # absent (early-format history notes pre-XML-standardisation), the verb
    # can appear in one of two structured alternatives (AGENTS §2.4 single-
    # predicate family; AGENTS §1.10 -- a real amend verb must not fall to
    # __missing__ when its surface form is recoverable):
    #
    # Shape A (5 rows on act_public_1956_47 @ 2001-10-02):
    #   <amended-provision>Section 19I(1)</amended-provision>:
    #   <amended-provision>amended</amended-provision>, on ...
    # A SECOND <amended-provision> element whose text classifies as a known
    # operation family. The legacy editorial-consolidation XML reuses the
    # <amended-provision> tag for the verb phrase.
    #
    # Shape D (1 row on act_public_1876_79):
    #   Subsection <amended-provision>(5)</amended-provision> was
    #   <amended-provision>repealed</amended-provision>, as from ...
    # Same double-<amended-provision> shape -- covers the broader polarity.
    #
    # Shape B (1 row on act_public_1871_24):
    #   The words <quote.in>X</quote.in> were
    #   <amending-instruction>substituted</amending-instruction>, as from ...
    # The verb is in a non-standard <amending-instruction> element.
    if not operation:
        recovered = _recover_legacy_operation_from_amended_provision_node(node)
        if recovered:
            operation = recovered
            recovery_rule_id = (
                NZ_SOURCE_HISTORY_NOTE_LEGACY_AMENDED_PROVISION_VERB_RECOVERY_RULE_ID
            )
    if not operation:
        amending_instruction_text = _first_descendant_text(node, "amending-instruction")
        if amending_instruction_text.strip().lower() in _LEGACY_AMENDED_PROVISION_VERB_SYNONYMS:
            operation = amending_instruction_text.strip()
            recovery_rule_id = (
                NZ_SOURCE_HISTORY_NOTE_LEGACY_AMENDING_INSTRUCTION_VERB_RECOVERY_RULE_ID
            )
    return NZHistoryWitness(
        xml_id=_attr(node, "id"),
        xml_path=_element_source_key(node),
        text=text,
        amended_provision=amended_provision,
        operation=operation,
        amendment_date=amendment_date,
        amendment_date_iso=nz_date_text_to_iso(amendment_date),
        amending_provisions=tuple(_descendant_texts(node, "amending-provision")),
        amending_provision_hrefs=tuple(_descendant_attrs(node, "amending-provision", "href")),
        amending_legislation=_first_descendant_text(node, "amending-leg"),
        amending_work_id=work_id,
        defined_term=_history_note_defined_term(node),
        recovery_rule_id=recovery_rule_id,
    )


# Stable rule_ids for the two legacy-verb-recovery shapes emitted when the
# canonical ``<amending-operation>`` element was absent and the verb was
# recovered from a structured alternative (AGENTS §2.1 needs a stable
# rule_id + family tag + source witness + finding emission + strict-mode
# behavior + synthetic test + corpus regression when corpus-confirmed).
NZ_SOURCE_HISTORY_NOTE_LEGACY_AMENDED_PROVISION_VERB_RECOVERY_RULE_ID = (
    "nz_source_history_note_legacy_amended_provision_verb_recovery"
)
NZ_SOURCE_HISTORY_NOTE_LEGACY_AMENDING_INSTRUCTION_VERB_RECOVERY_RULE_ID = (
    "nz_source_history_note_legacy_amending_instruction_verb_recovery"
)


def _recover_legacy_operation_from_amended_provision_node(
    node: etree._Element,
) -> str:
    """Legacy history-note verb recovery for the double-``<amended-provision>``
    shape (Shapes A and D in the recovery notebook, 2026-06-27).

    Early-format NZ consolidated XML mislabels the operation verb as a
    SECOND ``<amended-provision>`` element rather than the canonical
    ``<amending-operation>`` element. Two confirmed shapes:

    * Shape A (5 rows on act_public_1956_47 @ 2001-10-02):

        <history-note>
          <amended-provision>Section 19I(1)</amended-provision>:
          <amended-provision>amended</amended-provision>, on
          <amendment-date>2 October 2001</amendment-date>, by
          <amending-provision ...>section 21</amending-provision> of the
          <amending-leg>...</amending-leg>.
        </history-note>

    * Shape D (1 row on act_public_1876_79):

        <history-note>
          Subsection <amended-provision>(5)</amended-provision> was
          <amended-provision>repealed</amended-provision>, as from
          <amendment-date>1 July 2003</amendment-date>, ...
        </history-note>

    Recovery: enumerate ALL ``<amended-provision>`` descendants; skip the
    FIRST (that is the canonical section label -- the amended_provision
    field); any SUBSEQUENT ``<amended-provision>`` whose text matches a
    known operation-family verb (incl. the 'revoked' synonym) is the
    recovered verb.

    Witnesses verified (2026-06-27 audit on the smoke corpus):

    * act_public_1956_47 nz-opw-244/245/246/255/257/305 (6 rows: Shape A)
    * act_public_1876_79 nz-opw-5 (Shape D)

    AGENTS §1.10 (distinct named diagnostic; a real amend verb must not
    fall to __missing__ when its surface form is recoverable) + §2.4
    single-predicate recovery family (one recogniser for the recurring
    shape, NOT a per-prose-sentence recognizer).
    """
    provisions = list(_descendant_texts(node, "amended-provision"))
    if len(provisions) < 2:
        return ""
    for candidate in provisions[1:]:
        if candidate.strip().lower() in _LEGACY_AMENDED_PROVISION_VERB_SYNONYMS:
            return candidate.strip()
    return ""


# Known NZ operation-family verbs (lowercased) plus the 'revoked'-as-repealed
# synonym. Used only for the legacy double-`<amended-provision>` recovery
# (a strict-superset check the canonical classify_operation_family would
# also pass once the verb is wired onto the witness). Kept locally in
# source_tree (NOT imported from operation_surface) to avoid a circular
# import: operation_surface imports source_tree via parse_nz_source_document.
_LEGACY_AMENDED_PROVISION_VERB_SYNONYMS = frozenset({
    "added",
    "amended",
    "brought into force",
    "editorial change",
    "expired",
    "inserted",
    "repealed",
    "revoked",
    "replaced",
    "substituted",
})


def _history_note_defined_term(node: etree._Element) -> str:
    """Extract the defined term a definition-level history note targets.
    Terms carrying the path separators ``/`` or ``:`` are dropped (cannot be a
    clean addressable label).
    """
    saw_amended_provision = False
    for child in node:
        if not isinstance(child.tag, str):
            continue
        local = _localname_of_tag(child.tag)
        if local == "amended-provision":
            saw_amended_provision = True
            continue
        if local == "amending-operation":
            break
        if local == "emphasis" and saw_amended_provision:
            term = _normalize_text(child.text or "")
            if term and "/" not in term and ":" not in term:
                return term
            return ""
    return ""


def _defpara_para_starts_new_definition(para: etree._Element) -> bool:
    """Whether a ``def-para``'s direct ``<para>`` child begins a NEW definition.

    A well-formed ``def-para`` holds exactly one definition: a single leading
    ``<para>`` whose first ``<text>`` opens with the defined ``<def-term>``,
    optionally followed by nested ``<label-para>`` limbs. Some amending acts pack
    several distinct definitions under ONE ``<def-para>`` element as a run of
    sibling direct ``<para>`` children, each opening with its OWN ``<def-term>``
    (e.g. 2020/62 LMS313899 packs "smokeless tobacco product" and "smoking
    cessation programme"); the official consolidation splits them into one
    ``def-para`` per definition, and each packed definition is its own insert
    witness here. Such a sibling ``<para>`` is identified by its FIRST element
    child being a ``<text>`` whose FIRST element child is a ``<def-term>`` — the
    leading-term shape of a definition opener. A second ``<def-term>`` that sits
    LATER in a definition's prose ("…and <def-term>advertising</def-term> has a
    corresponding meaning") is NOT a leading term and so is not mistaken for a
    new definition (that definition stays whole).
    """

    if not isinstance(para.tag, str) or _localname_of_tag(para.tag) != "para":
        return False
    for child in para:
        if not isinstance(child.tag, str):
            continue
        if _localname_of_tag(child.tag) != "text":
            # First element child is not a leading ``<text>`` — not a definition
            # opener (e.g. a leading ``<label-para>`` limb of the same defn).
            return False
        for grandchild in child:
            if not isinstance(grandchild.tag, str):
                continue
            return _localname_of_tag(grandchild.tag) == _DEF_TERM_TAG
        return False
    return False


def _defpara_owned_children(defpara: etree._Element) -> list[etree._Element]:
    """Direct children of a ``def-para`` that belong to its FIRST definition.

    Returns all direct children up to (but excluding) the first non-leading
    direct ``<para>`` child that opens a NEW definition (see
    :func:`_defpara_para_starts_new_definition`). For a well-formed single-
    definition ``def-para`` this is every direct child (unchanged behaviour); for
    a multi-definition packed ``def-para`` it bounds extraction to the targeted
    (first) definition so the adjacent definition's text is not absorbed.
    """

    owned: list[etree._Element] = []
    seen_definition = False
    for child in defpara:
        if (
            isinstance(child.tag, str)
            and _localname_of_tag(child.tag) == "para"
            and _defpara_para_starts_new_definition(child)
        ):
            if seen_definition:
                break
            seen_definition = True
        owned.append(child)
    return owned


def _legal_text(node: etree._Element, *, cache: dict[tuple[etree._Element, bool], str] | None = None) -> str:
    texts: list[str] = []
    if isinstance(node.tag, str) and _localname_of_tag(node.tag) == "def-para":
        # Bound a packed multi-definition ``def-para`` to its first definition so
        # an adjacent definition mispacked under the same element is not absorbed.
        for child in _defpara_owned_children(node):
            if not isinstance(child.tag, str):
                continue
            if _localname_of_tag(child.tag) in _TEXT_EXCLUDE_TAGS:
                continue
            text = _collect_legal_text(child, is_root=False, cache=cache)
            if text:
                texts.append(text)
            if child.tail:
                texts.append(child.tail)
        return _normalize_text(" ".join(texts))
    if cache is not None:
        cached = cache.get((node, True))
        if cached is not None:
            return cached
        if not (node.text or "").strip():
            cached = cache.get((node, False))
            if cached is not None:
                text = _normalize_text(cached)
                cache[(node, True)] = text
                return text
    text = _normalize_text(_collect_legal_text(node, is_root=True, cache=cache))
    if cache is not None:
        cache[(node, True)] = text
    return text


# Lettered-paragraph leaf kinds whose text may continue into a trailing
# label-less table ``<para>`` sibling. NZ amend payloads frequently emit an
# inline definitions/illustration table as a SEPARATE ``<para>`` sibling AFTER
# the lettered paragraph it belongs to (e.g. 2020/38 Schedule 26's
# "school boards:" paragraph followed by a sibling ``<para><legtable>…``), while
# the official consolidation nests that table INSIDE the paragraph's own
# ``<para>``. The trailing sibling carries no ``<label>`` and belongs to the
# preceding labelled item, so absorbing it into that item's text makes the amend
# payload match the consolidated form.
_TABLE_CONTINUATION_LEAF_KINDS = frozenset({"label-para", "subprov"})


def _is_table_continuation_para(element: etree._Element) -> bool:
    """Whether a sibling ``<para>`` is a table continuation of a preceding leaf.

    A table-continuation ``<para>`` carries NO ``<label>`` of its own (it is not
    a fresh lettered/numbered item) and contains a ``<legtable>`` (or bare
    ``<table>``). Such a para is presentational continuation of the labelled item
    it follows, not an independent provision. A ``<para>`` that opens a new
    labelled item (has a direct ``<label>`` child or wraps a labelled structural
    child) is NOT a continuation and stops the absorption.
    """

    if not isinstance(element.tag, str) or _localname_of_tag(element.tag) != "para":
        return False
    has_table = False
    for descendant in element.iter():
        if not isinstance(descendant.tag, str):
            continue
        local = _localname_of_tag(descendant.tag)
        if local in {"legtable", "table"}:
            has_table = True
        if local in _STRUCTURAL_TAGS:
            # A nested structural (labelled) child means this para introduces a
            # new item — not a pure table continuation of the preceding leaf.
            return False
    return has_table


def _trailing_table_continuation_text(
    leaf_element: etree._Element,
    *,
    cache: dict[tuple[etree._Element, bool], str] | None = None,
) -> str:
    """Flow text of label-less table ``<para>`` siblings trailing a leaf element.

    Walks the leaf element's following siblings and collects the legal text of
    each consecutive table-continuation ``<para>`` (see
    :func:`_is_table_continuation_para`), stopping at the first sibling that is
    not such a para (a new labelled item, prose, or end of parent). Returns the
    normalized concatenation, or ``""`` when there is no trailing table sibling.
    Only applied to lettered-paragraph leaf kinds by the caller.
    """

    parts: list[str] = []
    sibling = leaf_element.getnext()
    while sibling is not None:
        if not isinstance(sibling.tag, str):
            sibling = sibling.getnext()
            continue
        if not _is_table_continuation_para(sibling):
            break
        text = _legal_text(sibling, cache=cache)
        if text:
            parts.append(text)
        sibling = sibling.getnext()
    return _normalize_text(" ".join(parts))


def _walk_payload_root_nodes(matched_element: etree._Element) -> list[NZSourceNode]:
    """Parse an amend-payload match into nodes, absorbing trailing table siblings.

    Walks ``matched_element`` with the live-body walker (placeholder ``amend``
    path), then — when the matched leaf is a lettered-paragraph kind followed by
    a label-less table-continuation ``<para>`` sibling — appends that table's
    flow text to the root node's text so the payload matches the consolidated
    form (which nests the table inside the paragraph). Returns the node list
    (root first); an empty list signals an empty payload to the caller.
    """

    from dataclasses import replace as _dc_replace

    nodes: list[NZSourceNode] = []
    legal_text_cache: dict[tuple[etree._Element, bool], str] = {}
    amend_instruction_text_nodes_by_candidate = _amend_instruction_candidate_nodes(matched_element)
    _walk_source_nodes(
        matched_element,
        path=("amend",),
        nodes=nodes,
        attached_history_note_keys=set(),
        legal_text_cache=legal_text_cache,
        amend_instruction_text_nodes_by_candidate=amend_instruction_text_nodes_by_candidate,
    )
    if not nodes:
        return nodes
    if (
        isinstance(matched_element.tag, str)
        and _localname_of_tag(matched_element.tag) in _TABLE_CONTINUATION_LEAF_KINDS
    ):
        continuation = _trailing_table_continuation_text(matched_element, cache=legal_text_cache)
        if continuation:
            root = nodes[0]
            combined = _normalize_text(f"{root.text} {continuation}")
            nodes[0] = _dc_replace(root, text=combined)
    return nodes


def _collect_legal_text(
    node: etree._Element,
    *,
    is_root: bool,
    cache: dict[tuple[etree._Element, bool], str] | None = None,
) -> str:
    """Append a node's flow text in true document order.

    Emits ``node.text``, then each child's subtree text followed by that child's
    ``tail`` — the document-order interleave of element text and the text that
    trails inline elements. A flat ``iter()`` loop that appends ``text`` then
    ``tail`` per element mis-orders inline markup: an inline ``<extref>``/
    ``<intref>`` whose reference text sits inside a ``<citation>`` (the modern
    consolidated body shape) has its parent citation's ``tail`` appended before
    the inner reference text, so the cross-reference text floats to the end of
    the run (e.g. "required by, the Secretary ... section 47" instead of
    "required by section 47, the Secretary"). The same logical content arrives
    flat in an amending act's ``<amend>`` payload, so the two sides extracted
    different strings for identical text. Walking children in order, with each
    inline element's ``tail`` emitted after its subtree, makes both sides exact.

    ``_TEXT_EXCLUDE_TAGS`` subtrees (notes/history) contribute nothing — neither
    their text nor their ``tail`` — preserving the prior exclusion behaviour.
    """

    if not isinstance(node.tag, str):
        return ""
    if _localname_of_tag(node.tag) in _TEXT_EXCLUDE_TAGS:
        return ""
    if len(node) == 0:
        return "" if is_root else (node.text or "")
    key = (node, is_root)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached
        if is_root and not (node.text or "").strip():
            cached = cache.get((node, False))
            if cached is not None:
                return cached
    texts: list[str] = []
    # The structural root contributes only its descendant flow text, not its own
    # leading ``text`` (which for a structural element is empty/whitespace); this
    # matches the historical extraction so non-inline nodes are unchanged.
    if not is_root and node.text:
        texts.append(node.text)
    for child in node:
        if not isinstance(child.tag, str):
            # Comment/PI nodes contribute nothing (text or tail), matching the
            # historical extractor's ``isinstance(tag, str)`` skip.
            continue
        if _localname_of_tag(child.tag) in _TEXT_EXCLUDE_TAGS:
            # Excluded subtree: skip its text and the tail that trails it, to
            # keep the historical "notes/history contribute nothing" behaviour.
            continue
        child_text = (
            child.text or ""
            if len(child) == 0
            else _collect_legal_text(child, is_root=False, cache=cache)
        )
        if child_text:
            texts.append(child_text)
        if child.tail:
            texts.append(child.tail)
    text = " ".join(texts)
    if cache is not None:
        cache[key] = text
    return text


# Amend-subtree section disambiguation. --------------------------------------
#
# A single amending PROVISION (the history-note href resolves to one whole
# section of the amending act) typically carries SEVERAL operative ``<amend>``
# subtrees, one per instruction ("Replace section 81(1) with: …", "Replace
# section 88(1) to (4) with: …", "Replace section 91(2) with: …"). Each amend
# subtree lives in a ``<para>``/``<text>`` whose leading citation names the
# target SECTION it operates on. When two instructions touch sub-provisions with
# the SAME label in DIFFERENT sections (section 81(1) and section 84(1) both carry
# a subprov ``1``), a leaf-only match across the whole amending node is ambiguous.
# The witness, however, knows its target section (the top-level ``prov`` segment
# of its address). Filtering the candidate amend subtrees to the one whose cited
# section matches the witness's section is EXACT disambiguation — the citation
# extref text is authoritative, not a guess. When no leading citation parses (or
# the witness has no section context), the extractor falls back to the
# section-agnostic leaf match, so behaviour is unchanged where disambiguation is
# neither possible nor needed.

# Leading instruction-citation prefixes that introduce a target label. Matched
# case-insensitively against the cited target text ("section 88(1) to (4)").
_AMEND_INSTRUCTION_SECTION_RE = re.compile(
    r"^\s*(?:new\s+)?"
    r"(?:sections?|ss?|clauses?|cls?|schedules?|sch|parts?|regulations?|regs?|rules?|articles?|arts?)\s+"
    r"([0-9]+[A-Za-z]*)",
    re.IGNORECASE,
)


def _amend_subtree_section_label(amend_element: etree._Element) -> str | None:
    """Top-level target label of the instruction that introduces ``amend_element``.

    Walks up from the ``<amend>`` to the nearest enclosing instruction context and
    reads the leading ``<text>`` citation ("Replace section 88(1) to (4) with:")
    that precedes the amend subtree, returning the cited top-level provision label
    ("88"). Returns ``None`` when no preceding citation with a parseable section
    label is found — the caller then falls back to leaf-only matching (no guess).
    """

    cursor: etree._Element | None = amend_element
    while cursor is not None:
        parent = cursor.getparent()
        if parent is None:
            break
        for child in parent:
            if child is cursor:
                break
            if not isinstance(child.tag, str) or _localname_of_tag(child.tag) != "text":
                continue
            cited = _amend_instruction_target(child)
            if not cited:
                continue
            match = _AMEND_INSTRUCTION_SECTION_RE.match(cited)
            if match:
                return _normalize_text(match.group(1))
        cursor = parent
    return None


# Structural-replacement (whole-provision substitute) extraction. ------------
#
# A structural ``replaced``/``substituted`` instruction in an amending act reads
# "section N is repealed and the following ... substituted:" / "Replace section N
# with:" followed by a typed ``<amend>`` subtree carrying the NEW provision body.
# Unlike ``<amend.in>`` inline text, the new content is one (or more) structural
# child nodes. This extractor reads the ``<amend>`` structural child whose kind +
# label match the target leaf and parses it into an ``NZSourceNode`` replacement
# subtree — the exact same node model the live body uses — so the dry-run REPLACE
# kernel can substitute it for the resolved target.
#
# A one-to-many "substitute the following subsections/sections" expansion is
# accepted: the amend subtree carries several structural children (e.g. subprov
# 2, 3, 4) but each affected child is its OWN upstream history-note witness with
# its own ``amended-provision`` label, so the per-witness target leaf keys
# exactly one of them. Selecting that single child is a clean per-witness
# extraction, NOT a flatten — the sibling children are replaced/inserted by their
# own witnesses. The extractor stays conservative: zero matching children, or
# more than one child matching the SAME leaf (genuine ambiguity), or an empty
# extraction, are all typed blockers. It never guesses which child is the
# replacement.

# Typed blocker reasons for structural-replacement extraction. Each is a fact
# about why the amending payload is not a clean one-to-one replacement; none is a
# guess or a silent drop.
NZ_STRUCTURAL_REPLACE_BLOCKED_NO_AMEND_SUBTREE = "structural_replace_no_amend_subtree_in_amending_node"
NZ_STRUCTURAL_REPLACE_BLOCKED_NO_MATCHING_CHILD = "structural_replace_no_amend_child_matches_target_leaf"
NZ_STRUCTURAL_REPLACE_BLOCKED_AMBIGUOUS_MATCH = "structural_replace_multiple_amend_children_match_target_leaf"
# Single-match-wrong-section typed receipt (§2.5 audit state; forward-looking).
# When exactly ONE <amend> child matches the target leaf's kind+label BUT that
# amend subtree's enclosing instruction citation names a DIFFERENT section than
# the op's target_provision_label, the single match is semantically wrong -- it
# belongs to a different section's amend. Emitting the typed blocker prevents
# silently accepting the wrong-section payload (AGENTS §1.1 no silent target
# hijacking). When the amend subtree's section label is unparseable (None),
# we still accept (no disambiguating evidence to block on).
NZ_STRUCTURAL_REPLACE_BLOCKED_SINGLE_MATCH_WRONG_SECTION = (
    "structural_replace_single_match_wrong_section"
)
NZ_STRUCTURAL_INSERT_BLOCKED_SINGLE_MATCH_WRONG_SECTION = (
    "structural_insert_single_match_wrong_section"
)
NZ_STRUCTURAL_REPLACE_BLOCKED_EMPTY_REPLACEMENT = "structural_replace_extracted_replacement_is_empty"
NZ_STRUCTURAL_REPLACE_BLOCKED_TARGET_LEAF_UNUSABLE = "structural_replace_target_leaf_kind_or_label_unusable"


@dataclass(frozen=True)
class NZStructuralReplacement:
    """A cleanly-extracted whole-provision structural replacement payload.

    ``root`` is the new replacement node read from the amending act's ``<amend>``
    subtree; ``descendants`` are its nested structural nodes (subprovs, label-
    paras, def-paras) parsed with the same walker as the live body, so the
    replacement subtree is byte-comparable to an on-or-after oracle subtree.
    ``root`` and ``descendants`` carry placeholder ``amend/...`` paths; the
    REPLACE kernel re-roots them onto the resolved target's live-body path.
    """

    root: NZSourceNode
    descendants: tuple[NZSourceNode, ...]

    @property
    def nodes(self) -> tuple[NZSourceNode, ...]:
        return (self.root, *self.descendants)


def extract_structural_replacement(
    amending_node: etree._Element,
    *,
    target_leaf_kind: str,
    target_leaf_label: str,
    target_provision_label: str | None = None,
    base_work_year: str = "",
    base_work_number: str = "",
    amending_act_root: etree._Element | None = None,
    schedule_indirection_cache: dict[tuple[object, ...], object] | None = None,
) -> "NZStructuralReplacement | str":
    """Extract a clean one-to-one structural replacement from an amending node.

    ``amending_node`` is the amending act provision element resolved from the
    history-note ``amending-provision`` href. ``target_leaf_kind`` /
    ``target_leaf_label`` describe the live-body node the instruction replaces
    (e.g. ``prov``/``41`` for "section 41", ``subprov``/``4`` for "section
    28(4)"). ``target_provision_label`` is the witness's top-level section label
    ("88" for "section 88(4)"); when given it disambiguates across the several
    ``<amend>`` subtrees an amending section carries (one per instruction) by the
    cited section, so a sub-provision label shared by two instructions in
    different sections is no longer ambiguous.

    Returns an :class:`NZStructuralReplacement` when EXACTLY ONE structural child
    across the (optionally section-filtered) ``<amend>`` subtree(s) matches the
    target leaf's kind+label — whether the amend carries one child or a one-to-many
    expansion whose other children belong to sibling witnesses. Otherwise returns
    a typed blocker reason string (never a guess, never a flatten). More than one
    child matching the SAME leaf, even after section disambiguation, is genuine
    ambiguity and stays blocked.

    ``amending_act_root`` enables the amending-act-own-schedule delegation
    path (AGENTS §2.5 extension of the canonical structural-leaf recognizer
    family): when the inline ``<amend>``-subtree path produces a no-payload
    blocker AND the amending provision delegates its payload by prose to
    "Schedule K of this Act", the payload is fetched from the amending act's
    own top-level ``<schedule>`` K carrier. When ``None`` (legacy callers)
    the inline blockers stand unchanged.
    """

    if not target_leaf_kind or not target_leaf_label:
        return NZ_STRUCTURAL_REPLACE_BLOCKED_TARGET_LEAF_UNUSABLE
    normalized_label = _normalize_text(target_leaf_label)
    normalized_provision = _normalize_text(target_provision_label) if target_provision_label else ""

    # A schedule-indirection amending provision delivers its replacement payload
    # through schedule tables, not its own ``<amend>`` subtree. When the base work
    # is known we follow the indirection into the schedule amendment group keyed to
    # that act; otherwise the plain "no amend subtree" blocker below stands.
    if _amending_node_is_schedule_indirection(
        amending_node,
        cache=schedule_indirection_cache,
    ):
        return _resolve_schedule_indirection(
            amending_node,
            leaf_kind=target_leaf_kind,
            normalized_label=normalized_label,
            normalized_provision=normalized_provision,
            base_work_year=base_work_year,
            base_work_number=base_work_number,
            insertion=False,
            schedule_indirection_cache=schedule_indirection_cache,
        )

    amend_subtrees = [
        element
        for element in amending_node.iter()
        if isinstance(element.tag, str) and _localname_of_tag(element.tag) == "amend"
    ]
    if not amend_subtrees:
        # AGENTS §2.5: try the amending-act-own-schedule delegation form
        # (canonical typed parser/recognizer family extension). When the
        # amending provision delegates by prose to "Schedule K of this
        # Act" and the amending act root is available, follow the directive
        # into the carrier schedule. Otherwise the no-amend-subtree blocker
        # stands.
        delegated = _try_amending_act_own_schedule_delegation(
            amending_node,
            amending_act_root,
            target_leaf_kind=target_leaf_kind,
            normalized_label=normalized_label,
            insertion=False,
        )
        if delegated is not None:
            return delegated
        return NZ_STRUCTURAL_REPLACE_BLOCKED_NO_AMEND_SUBTREE

    # Select the single ``<amend>`` structural child whose kind+label match the
    # witness's target leaf. A one-to-many expansion ("repealing section 81, and
    # substituting the following sections: 81 81AA 81AB 81AC" / "substitute the
    # following subsections: (2) (3) (4)") is already decomposed UPSTREAM into one
    # history-note witness per affected child — each replaced child carries its own
    # ``amended-provision`` reference with its own label. So a multi-child amend is
    # NOT a flatten for this witness: the per-witness target leaf keys exactly one
    # child, and the sibling children belong to OTHER witnesses (their own replace
    # or, for newly-added labels, insert ops). We therefore match at the child
    # level uniformly — exactly one matching child (in a single- OR multi-child
    # amend) is a clean extraction; more than one child matching the SAME leaf is
    # genuine ambiguity and stays blocked; no matching child stays blocked.
    matches = _replacement_leaf_matches(amend_subtrees, target_leaf_kind, normalized_label)

    if len(matches) > 1 and normalized_provision:
        # The same sub-provision label is matched in more than one amend subtree
        # (different sections amended by the same amending section). Restrict to
        # the amend subtree(s) whose cited section equals the witness's section —
        # exact disambiguation by the instruction citation, never a guess.
        section_scoped = [
            amend
            for amend in amend_subtrees
            if _amend_subtree_section_label(amend) == normalized_provision
        ]
        if section_scoped:
            scoped_matches = _replacement_leaf_matches(section_scoped, target_leaf_kind, normalized_label)
            if scoped_matches:
                matches = scoped_matches

    if not matches:
        # AGENTS §2.5: schedule-as-direct-payload delegation retry (see the
        # no-amend-subtree branch above for the same shape).
        delegated = _try_amending_act_own_schedule_delegation(
            amending_node,
            amending_act_root,
            target_leaf_kind=target_leaf_kind,
            normalized_label=normalized_label,
            insertion=False,
        )
        if delegated is not None:
            return delegated
        return NZ_STRUCTURAL_REPLACE_BLOCKED_NO_MATCHING_CHILD
    if len(matches) > 1:
        return NZ_STRUCTURAL_REPLACE_BLOCKED_AMBIGUOUS_MATCH

    # §2.5 audit-state: when target_provision_label was provided AND the single
    # match's enclosing instruction citation names a different section, the match
    # is semantically wrong -- the single amend child belongs to a different
    # section's amend subtree. Block with a distinct typed receipt rather than
    # silently accepting the wrong-section payload (AGENTS §1.1). When the
    # single match's section label is None (unparseable), accept (no
    # disambiguating evidence to block on -- the current default).
    if normalized_provision:
        match_section = _amend_subtree_section_label(matches[0])
        if match_section is not None and match_section != normalized_provision:
            return NZ_STRUCTURAL_REPLACE_BLOCKED_SINGLE_MATCH_WRONG_SECTION

    replacement_element = matches[0]
    nodes = _walk_payload_root_nodes(replacement_element)
    if not nodes:
        return NZ_STRUCTURAL_REPLACE_BLOCKED_EMPTY_REPLACEMENT
    root = nodes[0]
    if not root.text.strip():
        return NZ_STRUCTURAL_REPLACE_BLOCKED_EMPTY_REPLACEMENT
    return NZStructuralReplacement(root=root, descendants=tuple(nodes[1:]))


# Target-leaf kind aliases for amend-child matching. NZ XML encodes the SAME
# logical lettered sub-item inconsistently across the body and the amend payload:
# a sub-item addressed (via the history note) as a ``subprov`` may be carried in
# the amending act's ``<amend>`` subtree as a ``label-para`` and vice versa (both
# wrap their label in a ``<label>`` element and share the lettered-label space).
# The target leaf's KIND can therefore be too strict while the LABEL is exact and
# the payload is present. This explicit, symmetric alias set relaxes ONLY the kind
# comparison for these two interchangeable lettered-paragraph kinds; the label
# still must match exactly, so the >1-match ambiguity refusal (a ``subprov a`` AND
# a ``label-para a`` both present) still holds — no false positives. Coincidental
# numeric-label collisions across different structural levels (a ``schedule 3`` vs
# a ``subprov 3``, a ``part 2`` vs a ``subprov 2``) are deliberately NOT aliased.
_TARGET_LEAF_KIND_ALIASES: dict[str, frozenset[str]] = {
    "subprov": frozenset({"label-para"}),
    "label-para": frozenset({"subprov"}),
}


def _kind_matches_target_leaf(child_kind: str, target_leaf_kind: str) -> bool:
    """Whether an amend child's kind matches the target leaf kind (with aliases).

    Exact-kind match, or a kind in the target leaf's explicit alias set (the
    interchangeable lettered-paragraph kinds ``subprov``/``label-para``).
    """

    if child_kind == target_leaf_kind:
        return True
    return child_kind in _TARGET_LEAF_KIND_ALIASES.get(target_leaf_kind, frozenset())


def _amend_child_matches_leaf(child: etree._Element, target_leaf_kind: str, normalized_label: str) -> bool:
    child_kind = _localname_of_tag(child.tag) if isinstance(child.tag, str) else _localname(child)
    if not _kind_matches_target_leaf(child_kind, target_leaf_kind):
        return False
    # Read the label by the CHILD's own kind, not the target's: a ``def-para``
    # carries its label as a defined term, every other kind carries a ``<label>``.
    if child_kind == "def-para":
        child_label = _first_def_term(child)
    else:
        child_label = _direct_child_text(child, "label")
    return _normalize_text(child_label) == normalized_label


# Descendant-matching (nested-payload) lane. ---------------------------------
#
# In a structural replace/insert the new/target leaf often lives INSIDE a newly-
# inserted Part or section in the amend subtree (e.g. a new section ``147A``
# nested in a new ``<part>``/``<subpart>``, or a new subsection nested in a new
# section), not as a DIRECT structural child of the ``<amend>``. The top-level
# matchers above only reach the amend's direct children, so these payloads are
# fully present yet block as ``no_amend_child_matches_*``.
#
# The descendant lane recurses ONLY through the structural CONTAINER kinds a new
# provision can be wrapped in (``part``/``subpart``/``prov``) to find the target
# leaf nested below them. It is a strict FALLBACK: the caller tries the top-level
# matchers first and only descends when they find nothing, so the existing
# top-level path is byte-identical where it already fires. The >1-match ambiguity
# refusal is preserved end-to-end — if the leaf label is matched under more than
# one container the caller still refuses (never guesses which). Recursion is
# bounded to the container kinds so a leaf is never double-counted: the target
# leaf kind (``prov``/``subprov``/lettered-para) is distinct from the container
# kinds we recurse through, and a matched leaf is not itself recursed into.

# Structural kinds that can WRAP a newly-inserted provision in an amend subtree.
# These are the only kinds the descendant lane recurses through; the target leaf
# is found among their descendants.
_AMEND_CONTAINER_KINDS = frozenset({"part", "subpart", "prov"})


def _descend_container_leaf_matches(
    amend_subtrees: list[etree._Element],
    leaf_kind: str,
    normalized_label: str,
) -> list[etree._Element]:
    """Leaf matches nested below a new part/subpart/section in the amend subtrees.

    Recurses ONLY through the structural container kinds a new provision is
    wrapped in (``part``/``subpart``/``prov``) and returns every structural node
    nested below such a container whose kind+label match the target leaf. A node
    that is itself the matched leaf is not recursed into, so a leaf is counted
    once. Direct top-level amend children are NOT collected here (the top-level
    matchers own them); only genuinely nested leaves are returned. The caller
    treats >1 match as ambiguous and refuses — never a guess.
    """

    matches: list[etree._Element] = []

    def _recurse(element: etree._Element, *, inside_container: bool) -> None:
        for child in element:
            if not isinstance(child.tag, str):
                continue
            child_kind = _localname_of_tag(child.tag)
            if child_kind not in _STRUCTURAL_TAGS and child_kind not in _AMEND_CONTAINER_KINDS:
                # Non-structural wrapper (``<para>``, ``<text>`` etc.): keep
                # descending so a container nested under prose markup is reached,
                # without treating the wrapper itself as a container.
                _recurse(child, inside_container=inside_container)
                continue
            # A nested leaf match: only count it when it sits below a container
            # (i.e. it is genuinely nested, not a direct top-level amend child).
            if inside_container and _amend_child_matches_leaf(child, leaf_kind, normalized_label):
                matches.append(child)
                # Do not recurse into the matched leaf — its own sub-nodes are
                # part of the payload, never a separate match for this leaf.
                continue
            if child_kind in _AMEND_CONTAINER_KINDS:
                _recurse(child, inside_container=True)
            else:
                _recurse(child, inside_container=inside_container)

    for amend in amend_subtrees:
        _recurse(amend, inside_container=False)
    return matches


def _replacement_leaf_matches(
    amend_subtrees: list[etree._Element],
    target_leaf_kind: str,
    normalized_label: str,
) -> list[etree._Element]:
    """``<amend>`` structural children matching the target leaf (nested-aware).

    Tries the amend's DIRECT top-level structural children first; if none match,
    falls back to the descendant lane, which finds the target leaf nested below a
    newly-inserted ``part``/``subpart``/``section`` in the amend subtree. A single
    match (top-level or nested) is a clean extraction; more than one is ambiguous;
    none is blocked. The caller may pass a section-scoped subset of amend subtrees
    for disambiguation. The top-level path is byte-identical to the historical
    behaviour where it already fires — the nested lane is consulted only when the
    top level is empty, so no previously-clean extraction changes.
    """

    matches: list[etree._Element] = []
    for amend in amend_subtrees:
        for child in amend:
            if not isinstance(child.tag, str):
                continue
            if _localname_of_tag(child.tag) not in _STRUCTURAL_TAGS:
                continue
            if _amend_child_matches_leaf(child, target_leaf_kind, normalized_label):
                matches.append(child)
    if matches:
        return matches
    return _descend_container_leaf_matches(amend_subtrees, target_leaf_kind, normalized_label)


def _insertion_leaf_matches(
    amend_subtrees: list[etree._Element],
    inserted_leaf_kind: str,
    normalized_label: str,
) -> list[etree._Element]:
    """``<amend>`` structural nodes matching the inserted leaf's kind+label.

    For a ``def-para`` insert the new definition is frequently wrapped in an
    intermediate ``<para>`` ("insert, in their appropriate alphabetical order,
    the following definitions:") inside ``<amend>`` rather than being a direct
    amend child; the defined term is globally unique within an interpretation
    provision, so a descendant search keyed on the term is safe. Every other kind
    is matched as a direct top-level structural child first, then — only when no
    direct child matches — via the nested-payload descendant lane (a new section
    inside a new ``part``/``subpart``, a new subsection inside a new section). The
    caller may pass a section-scoped subset of amend subtrees for disambiguation
    and treats >1 match as ambiguous (refuses, never guesses).
    """

    matches: list[etree._Element] = []
    if inserted_leaf_kind == "def-para":
        for amend in amend_subtrees:
            for descendant in amend.iter():
                if not isinstance(descendant.tag, str):
                    continue
                if _localname_of_tag(descendant.tag) != "def-para":
                    continue
                if _amend_child_matches_leaf(descendant, inserted_leaf_kind, normalized_label):
                    matches.append(descendant)
        return matches
    for amend in amend_subtrees:
        for child in amend:
            if not isinstance(child.tag, str):
                continue
            if _localname_of_tag(child.tag) not in _STRUCTURAL_TAGS:
                continue
            if _amend_child_matches_leaf(child, inserted_leaf_kind, normalized_label):
                matches.append(child)
    if matches:
        return matches
    return _descend_container_leaf_matches(amend_subtrees, inserted_leaf_kind, normalized_label)


# Inserted-node (whole-provision INSERT) payload extraction. -------------------
#
# An ``inserted`` instruction in an amending act reads "the following section is
# inserted:" / "After section N, insert:" followed by a typed ``<amend>`` subtree
# carrying the NEW provision body. Unlike a REPLACE, the new node has no live-body
# target to swap; it is ADDED next to an anchor sibling. The new content is one
# (or more) structural child nodes under an ``<amend>`` subtree, the same node
# model the live body uses.
#
# This extractor reads the ``<amend>`` child whose kind/label match the inserted
# node's own (label, kind) and parses it into an ``NZSourceNode`` subtree. Unlike
# the REPLACE extractor, it INTENTIONALLY accepts a multi-child ``<amend>`` subtree
# ("the following sections are inserted: 18A ... 18B ...") because each inserted
# node is its own history-note witness with its own label — so pulling the single
# child whose label matches the witness is a clean one-node extraction, not a
# one-to-many flatten. It still refuses (typed blocker) when no child matches, when
# more than one child matches the same label (genuinely ambiguous), or when the
# extracted node is empty. It never guesses which child is the inserted node.

NZ_STRUCTURAL_INSERT_BLOCKED_NO_AMEND_SUBTREE = "structural_insert_no_amend_subtree_in_amending_node"
NZ_STRUCTURAL_INSERT_BLOCKED_NO_MATCHING_CHILD = "structural_insert_no_amend_child_matches_inserted_leaf"
NZ_STRUCTURAL_INSERT_BLOCKED_AMBIGUOUS_MATCH = "structural_insert_multiple_amend_children_match_inserted_leaf"
NZ_STRUCTURAL_INSERT_BLOCKED_EMPTY_PAYLOAD = "structural_insert_extracted_node_is_empty"
NZ_STRUCTURAL_INSERT_BLOCKED_LEAF_UNUSABLE = "structural_insert_inserted_leaf_kind_or_label_unusable"
NZ_STRUCTURAL_INSERT_BLOCKED_SCHEDULE_INDIRECTION = "structural_insert_amending_provision_is_schedule_indirection"
# Schedule-indirection where the schedule payload could not be resolved for the
# base work: the operative provision delegates to a schedule but no schedule
# amendment group keyed to the base act, or no matching ``<amend>`` for the
# target leaf within it, was found. Distinct from the plain "no schedule support"
# blocker so the residue is attributable to the schedule lane, never a guess.
NZ_STRUCTURAL_BLOCKED_SCHEDULE_GROUP_UNRESOLVED = "structural_schedule_indirection_no_amend_group_for_base_work"
NZ_STRUCTURAL_BLOCKED_SCHEDULE_NO_MATCHING_CHILD = "structural_schedule_indirection_no_amend_child_matches_target_leaf"
NZ_STRUCTURAL_BLOCKED_SCHEDULE_AMBIGUOUS_MATCH = "structural_schedule_indirection_multiple_amend_children_match_target_leaf"
# The schedule payload carries an unresolved ``[standard text]`` placeholder that
# the operative provision instructs to substitute (the Secondary Legislation Act
# 2021 omnibus shape). The substituted form, not the placeholder form, is what the
# oracle holds, so emitting the raw payload would be a known-wrong node. Resolving
# the substitution is a separate grammar; until then this is typed residue.
NZ_STRUCTURAL_BLOCKED_SCHEDULE_UNRESOLVED_PLACEHOLDER = "structural_schedule_indirection_payload_has_unresolved_placeholder"

# The placeholder token the omnibus operative provision substitutes. Matched on
# the flattened payload text ("... is [standard text].").
_SCHEDULE_PAYLOAD_PLACEHOLDER = re.compile(r"\[\s*standard text\s*\]", re.IGNORECASE)

# Amending-act-own-schedule delegation typed blockers.
#
# A second delegation form exists (distinct from the omnibus
# ``schedule.amendments.group2`` path above): the amending act's operative
# provision says "Replace Schedule N with the Schedule N set out in Schedule K
# of this Act" / "After Schedule M, insert the Schedule M' set out in
# Schedule K of this Act" — i.e. the payload is NOT inside ``<amend>`` at
# all, but lives in the AMENDING ACT'S OWN top-level ``<schedule>`` element
# K. The carrier schedule K wraps the new structural leaf (a nested
# ``<schedule>`` carrying the target leaf label, sometimes directly, sometimes
# inside an ``<amend>`` payload wrapper). The extractors below follow that
# delegation when the inline ``<amend>``-subtree path fails with the no-payload
# blockers and the directive prose witnesses it.
NZ_STRUCTURAL_BLOCKED_AMENDING_ACT_SCHEDULE_NOT_FOUND = (
    "structural_amending_act_named_schedule_not_found"
)
NZ_STRUCTURAL_BLOCKED_AMENDING_ACT_SCHEDULE_NO_MATCH = (
    "structural_amending_act_named_schedule_no_amend_child_matches_target_leaf"
)
NZ_STRUCTURAL_BLOCKED_AMENDING_ACT_SCHEDULE_AMBIGUOUS_MATCH = (
    "structural_amending_act_named_schedule_multiple_amend_children_match_target_leaf"
)

# Directive predicate: "Schedule K of this Act" references the AMENDING ACT's
# own top-level schedule K as the payload carrier. Compile-once at module
# scope (AGENTS §2.4 backtracking discipline); the matched index is the
# carrier schedule's ``<label>`` value.
_AMENDING_ACT_OWN_SCHEDULE_DIRECTIVE = re.compile(
    r"\bSchedule\s+([0-9]+[A-Za-z]*)\s+of\s+this\s+Act\b",
    re.IGNORECASE,
)

# Stronger directive form that ALSO names the carrier's structural-leaf kind:
# "the Part set out in Schedule K of this Act" / "the Schedule 2A set out in
# Schedule K of this Act". The kind word tells us the SHAPE of the payload the
# amending act delegates to its own schedule — used by the caller to scope the
# directive so it does NOT fire for sibling INLINE ``<amend>`` instructions
# sourced from the same amending provision (an amending prov that mixes one
# delegation instruction with many inline amendments would otherwise have the
# delegation trigger for every op, producing false-positive
# ``structural_amending_act_named_schedule_no_amend_child_matches_target_leaf``
# blockers where the carrier's payload kind does not match the inline op's
# target leaf). Group 1 = kind word; group 2 = carrier schedule label. The
# kind word's own label is OPTIONAL: the prose may name the kind generically
# ("the Part set out in Schedule K of this Act" — a NEW Part whose label is
# implied) OR specifically ("the Schedule 2A set out in Schedule K of this
# Act" — replaces a specific named schedule).
_AMENDING_ACT_OWN_SCHEDULE_DIRECTIVE_WITH_KIND = re.compile(
    r"\b(Part|Subpart|Schedule|Section|clause)(?:\s+[0-9]+[A-Za-z]*)?\s+set out in\s+"
    r"Schedule\s+([0-9]+[A-Za-z]*)\s+of\s+this\s+Act\b",
    re.IGNORECASE,
)

# NZ drafting kind-word -> the canonical IRNode kind used in target_leaf_kind.
# Bounded to the kind words the directive-with-kind regex actually captures;
# "Section"/"clause" both map to ``prov`` (NZ's statutory-section kind) because
# schedules use "clause" interchangeably with "section" for the same prov-level
# leaf (the source's own structural-kind alias, not a label coincidence).
_AMENDING_ACT_SCHEDULE_KIND_WORD_TO_IR_KIND: dict[str, str] = {
    "part": "part",
    "subpart": "subpart",
    "schedule": "schedule",
    "section": "prov",
    "clause": "prov",
}

# Schedule-indirection amending provisions ("Amend the Acts set out in the
# tables in Schedules 1 to 32 of this Act, in each case,—" / "Amend the
# enactments specified in Schedule N ... as set out in that schedule") deliver
# their payload through schedule TABLES, not through the ``<amend>`` subtree the
# history-note href points to. The href resolves to the amending act's OPERATIVE
# section, whose own illustrative body nodes would be spuriously matched. The
# real payload lives in ``<schedule.amendments.group2>`` blocks (one per amended
# act, sometimes wrapped in a ``<legtable>``) elsewhere in the amending act. When
# the base work is known we follow the indirection to the group keyed to that
# act and extract from its ``<amend>`` subtrees with the SAME leaf-matchers the
# inline path uses; otherwise we still refuse with a typed blocker.
#
# The detector matches the omnibus shapes seen across the corpus: "Acts" /
# "enactments" / "legislation" / "provisions" "specified|set out|listed" in a
# "Schedule(s) N", optionally with "as set out in that schedule" / "in the
# manner set out". The bare ``amend the acts set out ... schedules`` form is kept
# as a fast-path so existing behaviour is unchanged where it already fired.
_SCHEDULE_INDIRECTION_INSTRUCTION = re.compile(
    r"\bamend the acts?\s+set out\b.*\bschedules?\b", re.IGNORECASE | re.DOTALL
)
_SCHEDULE_INDIRECTION_DELEGATION = re.compile(
    r"\b(?:acts?|enactments?|legislation|provisions?|instruments?)\b"
    r".{0,80}?\b(?:specified|set out|listed)\b.{0,40}?\bschedules?\b",
    re.IGNORECASE | re.DOTALL,
)
_SCHEDULE_INDIRECTION_SET_OUT = re.compile(
    r"\b(?:as\s+)?set out in (?:that schedule|the (?:tables? in )?schedules?)\b"
    r"|\bin the manner (?:specified|set out)\b",
    re.IGNORECASE | re.DOTALL,
)


def _is_schedule_indirection_text(flat_text: str) -> bool:
    """True when an amending provision's prose delegates its payload to a schedule.

    Recognizes the bare ``amend the acts set out ... schedules`` form and the
    broader ``<kind> specified/set out in Schedule N`` + ``set out in that
    schedule`` / ``in the manner set out`` omnibus delegation. Conservative:
    requires both a schedule reference and a delegation verb so an incidental
    "schedule" mention is not mistaken for indirection.
    """
    if _SCHEDULE_INDIRECTION_INSTRUCTION.search(flat_text):
        return True
    if _SCHEDULE_INDIRECTION_DELEGATION.search(flat_text) and _SCHEDULE_INDIRECTION_SET_OUT.search(flat_text):
        return True
    return False


def _amending_node_is_schedule_indirection(
    amending_node: etree._Element,
    *,
    cache: dict[tuple[object, ...], object] | None = None,
) -> bool:
    key: tuple[object, ...] | None = None
    if cache is not None:
        root = amending_node.getroottree().getroot()
        key = ("is_schedule_indirection", id(root), amending_node.getroottree().getpath(amending_node))
        cached = cache.get(key)
        if isinstance(cached, bool):
            return cached
    for text_node in amending_node.iter():
        if not isinstance(text_node.tag, str) or _localname_of_tag(text_node.tag) != "text":
            continue
        if _is_schedule_indirection_text(_node_text(text_node)):
            if cache is not None and key is not None:
                cache[key] = True
            return True
    if cache is not None and key is not None:
        cache[key] = False
    return False


def _schedule_amendment_groups_for_base_work(
    amending_root: etree._Element,
    *,
    base_work_year: str,
    base_work_number: str,
) -> list[etree._Element]:
    """``<schedule.amendments.group2>`` blocks keyed to the base act.

    Each schedule-table amendment group carries a ``<heading>`` naming the amended
    act ("Forests Act 1949 (1949 No 19)" / "Corrections Act 2004 (2004 No 50)").
    We parse that citation and keep the groups whose (year, number) match the base
    work being replayed — an exact key, never a name-substring guess. A heading
    that does not parse to a public-act (year, number) (e.g. an SR-numbered
    regulation, or an empty continuation heading) is skipped: it cannot be the
    public-act base work. Returns every matching group across all schedules.
    """
    if not base_work_year or not base_work_number:
        return []
    groups: list[etree._Element] = []
    for group in amending_root.iter():
        if (
            not isinstance(group.tag, str)
            or _localname_of_tag(group.tag) != "schedule.amendments.group2"
        ):
            continue
        heading = _direct_child_text(group, "heading")
        if not heading:
            continue
        parsed = parse_public_act_citation(heading)
        if parsed is None:
            continue
        _title, year, number = parsed
        if year == base_work_year and number == base_work_number:
            groups.append(group)
    return groups


def _schedule_group_amend_subtrees(groups: list[etree._Element]) -> list[etree._Element]:
    """All ``<amend>`` payload subtrees inside the given schedule amendment groups.

    Within a group the ``<amend>`` subtrees sit either directly under instruction
    ``<para>``s ("After section 67C(1)(g)(iii), insert: <amend>…") or inside the
    rows of a ``<legtable>`` (location column + amendment column). A descendant
    scan reaches both shapes uniformly; the per-row instruction citation that
    precedes each ``<amend>`` is what later disambiguates by section, mirroring the
    inline ``_amend_subtree_section_label`` logic.
    """
    amends: list[etree._Element] = []
    for group in groups:
        for element in group.iter():
            if isinstance(element.tag, str) and _localname_of_tag(element.tag) == "amend":
                amends.append(element)
    return amends


def _schedule_amends_for_base_work(
    amending_root: etree._Element,
    *,
    base_work_year: str,
    base_work_number: str,
    cache: dict[tuple[object, ...], object] | None,
) -> list[etree._Element]:
    """Return schedule-indirection ``<amend>`` subtrees for one base work.

    This is a caller-owned performance cache for the exact same source facts that
    :func:`_schedule_amendment_groups_for_base_work` derives. It never authorizes
    replay: callers still run the normal structural leaf matcher and typed
    blockers over the returned subtrees.
    """
    if not base_work_year or not base_work_number:
        return []
    key = (
        "schedule_amends_for_base_work",
        id(amending_root),
        base_work_year,
        base_work_number,
    )
    if cache is not None:
        cached = cache.get(key)
        cached_amends = _cached_schedule_amends(cached)
        if cached_amends is not None:
            return cached_amends
    groups = _schedule_amendment_groups_for_base_work(
        amending_root,
        base_work_year=base_work_year,
        base_work_number=base_work_number,
    )
    amends = _schedule_group_amend_subtrees(groups)
    if cache is not None:
        cache[key] = tuple(amends)
    return amends


def _cached_schedule_amends(cached: object) -> list[etree._Element] | None:
    if not isinstance(cached, tuple):
        return None
    amends: list[etree._Element] = []
    for element in cached:
        if not isinstance(element, etree._Element):
            return None
        amends.append(element)
    return amends


def _extract_from_schedule_amends(
    amend_subtrees: list[etree._Element],
    *,
    leaf_kind: str,
    normalized_label: str,
    normalized_provision: str,
    insertion: bool,
) -> "NZStructuralReplacement | str":
    """Run the shared leaf-matchers over schedule-table ``<amend>`` subtrees.

    Reuses the inline insertion/replacement matchers so a schedule-delivered
    payload is parsed into the identical :class:`NZStructuralReplacement` node
    model. Disambiguates by the per-instruction section citation when more than
    one subtree matches the same leaf. Typed blockers (no match / ambiguous /
    empty) mirror the inline path; nothing is guessed.
    """
    match_fn = _insertion_leaf_matches if insertion else _replacement_leaf_matches
    matches = match_fn(amend_subtrees, leaf_kind, normalized_label)
    if len(matches) > 1 and normalized_provision:
        section_scoped = [
            amend
            for amend in amend_subtrees
            if _amend_subtree_section_label(amend) == normalized_provision
        ]
        if section_scoped:
            scoped_matches = match_fn(section_scoped, leaf_kind, normalized_label)
            if scoped_matches:
                matches = scoped_matches
    if not matches:
        return NZ_STRUCTURAL_BLOCKED_SCHEDULE_NO_MATCHING_CHILD
    if len(matches) > 1:
        return NZ_STRUCTURAL_BLOCKED_SCHEDULE_AMBIGUOUS_MATCH
    # The omnibus shape leaves a ``[standard text]`` placeholder in the schedule
    # payload that the operative provision substitutes; the oracle holds the
    # substituted form. Refuse the raw payload rather than emit a known-wrong node.
    if _SCHEDULE_PAYLOAD_PLACEHOLDER.search(_node_text(matches[0])):
        return NZ_STRUCTURAL_BLOCKED_SCHEDULE_UNRESOLVED_PLACEHOLDER
    nodes = _walk_payload_root_nodes(matches[0])
    if not nodes or not nodes[0].text.strip():
        return (
            NZ_STRUCTURAL_INSERT_BLOCKED_EMPTY_PAYLOAD
            if insertion
            else NZ_STRUCTURAL_REPLACE_BLOCKED_EMPTY_REPLACEMENT
        )
    return NZStructuralReplacement(root=nodes[0], descendants=tuple(nodes[1:]))


def _resolve_schedule_indirection(
    amending_node: etree._Element,
    *,
    leaf_kind: str,
    normalized_label: str,
    normalized_provision: str,
    base_work_year: str,
    base_work_number: str,
    insertion: bool,
    schedule_indirection_cache: dict[tuple[object, ...], object] | None,
) -> "NZStructuralReplacement | str":
    """Extract a schedule-delivered payload for the target leaf, or a typed blocker.

    Without the base work identity the payload cannot be keyed to its schedule
    group, so the caller's plain schedule-indirection blocker stands. With it we
    locate the ``<schedule.amendments.group2>`` block(s) for the base act and run
    the shared leaf-matchers over their ``<amend>`` subtrees.
    """
    if not base_work_year or not base_work_number:
        return (
            NZ_STRUCTURAL_INSERT_BLOCKED_SCHEDULE_INDIRECTION
            if insertion
            else NZ_STRUCTURAL_BLOCKED_SCHEDULE_GROUP_UNRESOLVED
        )
    amending_root = amending_node.getroottree().getroot()
    amend_subtrees = _schedule_amends_for_base_work(
        amending_root,
        base_work_year=base_work_year,
        base_work_number=base_work_number,
        cache=schedule_indirection_cache,
    )
    if not amend_subtrees:
        return NZ_STRUCTURAL_BLOCKED_SCHEDULE_GROUP_UNRESOLVED
    return _extract_from_schedule_amends(
        amend_subtrees,
        leaf_kind=leaf_kind,
        normalized_label=normalized_label,
        normalized_provision=normalized_provision,
        insertion=insertion,
    )


# Amending-act-own-schedule delegation. --------------------------------------
#
# Distinct from the ``schedule.amendments.group2`` omnibus indirection above
# (where the payload lives in BASE-act-keyed schedule amendment groups), the
# amending act's operative provision may delegate to its OWN top-level
# schedule: "Replace Schedule 2 with the Schedule 2 set out in Schedule 1 of
# this Act" -- the amending act's Schedule 1 carries the new Schedule 2
# content as a NESTED ``<schedule>`` structural child. The carrier takes two
# forms in the corpus:
#   1. ``<schedule><label>1</label><heading>New Schedule 2 of...</heading>``
#      ``<schedule><label>2</label>...</schedule></schedule>`` (direct nest).
#   2. ``<schedule><label>2</label><heading>Schedule 5 replaced</heading>``
#      ``<amend><schedule><label>5</label>...</schedule></amend></schedule>``
#      (the inner schedule is wrapped in an ``<amend>``).
#
# Followed through, the new structural leaf is the nested ``<schedule>``
# (sometimes ``<prov>`` for a sub-schedule insertion) whose kind+label match
# the witness's target leaf. The carrier wrapper itself is NOT the leaf;
# descendants past it are payload. AGENTS §2.4/§2.5: this is the canonical
# structural-leaf recognizer family EXTENDED to recognize the amending-act-
# own-schedule delegation form (one parser per family), never a parallel
# fallback. AGENTS §1.12: the payload is read from the AMENDING ACT's source
# XML (source faith), never the oracle.

# Kinds that mark "we are inside the amending-act-schedule payload carrier" --
# descending through any of these flips the inside-container flag for the
# leaf-match check. Extends the inline ``_AMEND_CONTAINER_KINDS`` set with
# ``schedule`` (the carrier wrapper itself) and ``amend`` (an intermediate
# payload wrapper inside the carrier). Both act as container gates here
# because the amending-act's schedule-N wrapper IS by directive the payload
# boundary; once we are inside it, every structural child is a candidate
# payload leaf.
_AMENDING_ACT_SCHEDULE_PAYLOAD_CONTAINERS = _AMEND_CONTAINER_KINDS | frozenset(
    {"schedule", "amend"}
)


def _amending_node_directs_to_amending_act_schedule(
    amending_node: etree._Element,
) -> tuple[str, str | None] | None:
    """Return the amending act's carrier-schedule label referenced by the
    directive ("Schedule K of this Act"), or ``None`` when no such directive
    is present.

    The directive form references the AMENDING ACT'S OWN top-level schedule
    (e.g. "the Schedule 2 set out in Schedule 1 of this Act" -- amending act's
    Schedule 1 is the carrier). The matched index is the carrier's ``<label>``
    value. The bare phrase "Schedule K of this Act" is a strong, witnessed
    signal (the prose explicitly delegates to the amending act's own
    schedule); it is NOT a guess about target scope -- it is a directive
    from the source itself. Returns ``None`` for amending provisions that do
    not delegate by this form (the inline ``<amend>``-subtree path stays in
    force).

    The STRONGER form ("the Part set out in Schedule K of this Act" /
    "the Schedule 2A set out in Schedule K of this Act") also names the
    carrier's structural-leaf kind; the returned tuple carries that kind as
    the second element (or ``None`` for the bare "Schedule K of this Act"
    form). The caller uses the kind hint to scope the directive to the
    specific instruction whose delegated payload shape matches the caller's
    ``target_leaf_kind`` -- without this, an amending provision that mixes
    one delegation instruction with many INLINE ``<amend>`` instructions
    would have the delegation trigger fire for every inline op, producing
    false-positive ``..._no_amend_child_matches_target_leaf`` blockers
    where the carrier's payload kind does not match the inline op's target
    leaf. AGENTS §1.0/§1.1: the kind hint is evidence-scoping of a directive
    the source already states, never a target-scope broadening.
    """

    for text_node in amending_node.iter():
        if not isinstance(text_node.tag, str) or _localname_of_tag(text_node.tag) != "text":
            continue
        flat_text = _node_text(text_node)
        kind_match = _AMENDING_ACT_OWN_SCHEDULE_DIRECTIVE_WITH_KIND.search(flat_text)
        if kind_match:
            kind_word = _normalize_text(kind_match.group(1)).lower()
            schedule_label = _normalize_text(kind_match.group(2))
            kind_hint = _AMENDING_ACT_SCHEDULE_KIND_WORD_TO_IR_KIND.get(kind_word)
            return (schedule_label, kind_hint)
        bare_match = _AMENDING_ACT_OWN_SCHEDULE_DIRECTIVE.search(flat_text)
        if bare_match:
            return (_normalize_text(bare_match.group(1)), None)
    return None


def _amending_act_top_level_schedule_by_label(
    amending_act_root: etree._Element,
    schedule_label: str,
) -> etree._Element | None:
    """Locate the amending act's top-level ``<schedule>`` whose ``<label>``
    equals ``schedule_label``.

    Top-level schedules appear either as direct children of the act root or
    inside a ``<schedule.group>`` wrapper. Only OUTER schedules are
    considered -- nested schedules inside a carrier are the payload, NOT
    carriers themselves, and an inner schedule's label can collide with an
    outer carrier's (the corpus has e.g. two ``<schedule><label>2</label>``
    elements where one is a carrier and one is its nested payload). Returns
    the FIRST outer match in document order; the directive prose witnesses
    the carrier label.
    """

    def _candidates() -> Iterable[etree._Element]:
        for child in amending_act_root:
            if not isinstance(child.tag, str):
                continue
            child_kind = _localname_of_tag(child.tag)
            if child_kind == "schedule":
                yield child
            elif child_kind == "schedule.group":
                for inner in child:
                    if isinstance(inner.tag, str) and _localname_of_tag(inner.tag) == "schedule":
                        yield inner

    normalized = _normalize_text(schedule_label)
    for schedule in _candidates():
        if _normalize_text(_direct_child_text(schedule, "label")) == normalized:
            return schedule
    return None


def _amending_act_schedule_descendant_matches(
    carrier: etree._Element,
    leaf_kind: str,
    normalized_label: str,
) -> list[etree._Element]:
    """Structural-leaf matches nested inside the amending-act carrier schedule.

    Recurses ONLY through the structural/payload-container kinds a delegated
    leaf can sit under (``schedule`` carrier itself / intermediate ``<amend>``
    wrappers / ``part`` / ``subpart`` / ``prov``) and returns every structural
    descendant whose kind+label match the target leaf (using the standard
    ``_amend_child_matches_leaf`` predicate so kind aliasing and label
    normalization are byte-identical to the inline path). The carrier wrapper
    enters the descent with ``inside_container=True`` (we are already inside
    the payload boundary by directive), so a DIRECT nested carrier child of
    the right kind+label is matched the same way a deeper one is. A matched
    leaf is not recursed into -- its sub-nodes are payload, never separate
    matches for this leaf. The caller treats >1 match as ambiguous and
    refuses (never a guess).
    """

    matches: list[etree._Element] = []

    def _recurse(element: etree._Element, *, inside_container: bool) -> None:
        for child in element:
            if not isinstance(child.tag, str):
                continue
            child_kind = _localname_of_tag(child.tag)
            structural = child_kind in _STRUCTURAL_TAGS
            container = child_kind in _AMENDING_ACT_SCHEDULE_PAYLOAD_CONTAINERS
            if not structural and not container:
                # Non-structural wrapper (``<para>``, ``<text>`` etc.): keep
                # descending so a container/payload nested under prose markup
                # is reached, without treating the wrapper itself as a
                # container.
                _recurse(child, inside_container=inside_container)
                continue
            if inside_container and _amend_child_matches_leaf(child, leaf_kind, normalized_label):
                matches.append(child)
                # Do not recurse into the matched leaf — its own sub-nodes
                # are part of the payload, never a separate match for this
                # leaf.
                continue
            _recurse(child, inside_container=container or inside_container)

    _recurse(carrier, inside_container=True)
    return matches


def _extract_from_amending_act_named_schedule(
    amending_act_root: etree._Element,
    schedule_label: str,
    target_leaf_kind: str,
    normalized_label: str,
    *,
    insertion: bool,
) -> "NZStructuralReplacement | str":
    """Extract the new structural leaf from the amending act's named carrier
    schedule, or emit a typed blocker.

    Resolves the carrier schedule by ``<label>`` equality with the directive
    reference (the prose "Schedule K of this Act" witnesses the carrier
    label), then runs the amending-act-schedule descendant matcher over it
    to find the nested structural child whose kind+label match the target
    leaf. Exactly one match produces an :class:`NZStructuralReplacement`
    (reusing the inline path's node model so materialization is byte-
    comparable); zero, multiple, or empty produces a typed blocker -- never
    a guess.
    """

    carrier = _amending_act_top_level_schedule_by_label(
        amending_act_root, schedule_label
    )
    if carrier is None:
        return NZ_STRUCTURAL_BLOCKED_AMENDING_ACT_SCHEDULE_NOT_FOUND
    matches = _amending_act_schedule_descendant_matches(
        carrier, target_leaf_kind, normalized_label
    )
    if not matches:
        return NZ_STRUCTURAL_BLOCKED_AMENDING_ACT_SCHEDULE_NO_MATCH
    if len(matches) > 1:
        return NZ_STRUCTURAL_BLOCKED_AMENDING_ACT_SCHEDULE_AMBIGUOUS_MATCH
    nodes = _walk_payload_root_nodes(matches[0])
    if not nodes or not nodes[0].text.strip():
        return (
            NZ_STRUCTURAL_INSERT_BLOCKED_EMPTY_PAYLOAD
            if insertion
            else NZ_STRUCTURAL_REPLACE_BLOCKED_EMPTY_REPLACEMENT
        )
    return NZStructuralReplacement(root=nodes[0], descendants=tuple(nodes[1:]))


def _try_amending_act_own_schedule_delegation(
    amending_node: etree._Element,
    amending_act_root: etree._Element | None,
    *,
    target_leaf_kind: str,
    normalized_label: str,
    insertion: bool,
) -> "NZStructuralReplacement | str | None":
    """Try the amending-act-own-schedule delegation form; return the result of
    the resolver when the directive matches, or ``None`` when it does not
    (so the caller falls through to its existing inline blocker).

    The delegation form requires BOTH the directive's prose (the amending
    provision's text contains "Schedule K of this Act") AND the amending act
    root element (so the carrier schedule can be located). When either is
    missing, this helper returns ``None`` and the caller's existing
    no-amend-subtree / no-matching-child blocker stands unchanged -- no silent
    state mutation, no parallel fallback (AGENTS §2.5). When the directive
    matches AND the amending act root is provided, the resolver's typed
    blocker or successful :class:`NZStructuralReplacement` is returned -- the
    caller MUST surface it to the dry-run refusal/refusal path so the typed
    blocker is visible in receipts.

    When the directive's prose names the carrier's payload kind ("the Part set
    out in Schedule K of this Act"), the kind hint is matched against
    ``target_leaf_kind`` via the canonical ``_kind_matches_target_leaf``
    predicate (reusing the ``subprov``/``label-para`` alias set). A mismatch
    returns ``None`` so the directive does NOT fire for sibling INLINE
    ``<amend>`` instructions sourced from the same amending prov -- the
    inline path runs unchanged, the existing blocker stands. This scoping
    is the owned fix for the false-positive
    ``structural_amending_act_named_schedule_no_amend_child_matches_target_leaf``
    blockers on amending provs that mix one schedule-delegation instruction
    with many inline amendments whose target-leaf kind differs from the
    carrier's payload kind (a directive that delegates "the Part set out in
    Schedule K of this Act" must NOT fire for an inline op whose target leaf
    is a ``prov``). AGENTS §1.0/§1.1: the kind hint narrows a directive the
    source already states; it never broadens target scope, never guesses.
    """

    if amending_act_root is None:
        return None
    directive = _amending_node_directs_to_amending_act_schedule(amending_node)
    if directive is None:
        return None
    schedule_label, carrier_target_kind_hint = directive
    if carrier_target_kind_hint is not None and not _kind_matches_target_leaf(
        carrier_target_kind_hint, target_leaf_kind
    ):
        # The directive's carrier payload kind (e.g. "Part") does not match
        # the caller's target-leaf kind (e.g. "prov"); the directive is for a
        # DIFFERENT instruction sourced from this amending prov. Do not fire
        # the schedule-delegation resolver; the inline ``<amend>``-subtree
        # path runs unchanged and its existing blocker stands.
        return None
    return _extract_from_amending_act_named_schedule(
        amending_act_root,
        schedule_label,
        target_leaf_kind,
        normalized_label,
        insertion=insertion,
    )


def extract_structural_insertion(
    amending_node: etree._Element,
    *,
    inserted_leaf_kind: str,
    inserted_leaf_label: str,
    target_provision_label: str | None = None,
    base_work_year: str = "",
    base_work_number: str = "",
    amending_act_root: etree._Element | None = None,
    schedule_indirection_cache: dict[tuple[object, ...], object] | None = None,
) -> "NZStructuralReplacement | str":
    """Extract the new provision node a whole-provision INSERT adds.

    ``amending_node`` is the amending act provision element resolved from the
    history-note ``amending-provision`` href. ``inserted_leaf_kind`` /
    ``inserted_leaf_label`` describe the NEW node the instruction inserts (e.g.
    ``prov``/``18A`` for "section 18A", ``part``/``5A`` for "Part 5A").
    ``target_provision_label`` is the witness's enclosing section label for a
    NESTED insert ("12" for a new "section 12(4A)"); when given it disambiguates
    across the several ``<amend>`` subtrees an amending section carries by the
    cited section, so a sub-provision label shared by two instructions in
    different sections is no longer ambiguous.

    Returns an :class:`NZStructuralReplacement` (reused as the new-node subtree
    carrier — ``root`` is the new node, ``descendants`` its nested structural
    nodes) when EXACTLY ONE ``<amend>`` child across the (optionally section-
    filtered) amending node matches the inserted leaf's kind+label; otherwise a
    typed blocker reason string. A multi-child ``<amend>`` subtree is allowed: the
    per-witness label selects the single inserted node, so this is a clean
    one-node extraction, never a flatten.

    ``amending_act_root`` enables the amending-act-own-schedule delegation
    path (see :func:`extract_structural_replacement`).
    """

    if not inserted_leaf_kind or not inserted_leaf_label:
        return NZ_STRUCTURAL_INSERT_BLOCKED_LEAF_UNUSABLE
    normalized_label = _normalize_text(inserted_leaf_label)
    normalized_provision = _normalize_text(target_provision_label) if target_provision_label else ""

    # A schedule-indirection amending provision does not carry the inserted node
    # in its own ``<amend>`` subtree (the content lives in schedule tables); its
    # body structural nodes would be spuriously matched by label. When the base
    # work is known we follow the indirection into the schedule amendment group
    # keyed to that act; otherwise we refuse with a typed blocker.
    if _amending_node_is_schedule_indirection(
        amending_node,
        cache=schedule_indirection_cache,
    ):
        return _resolve_schedule_indirection(
            amending_node,
            leaf_kind=inserted_leaf_kind,
            normalized_label=normalized_label,
            normalized_provision=normalized_provision,
            base_work_year=base_work_year,
            base_work_number=base_work_number,
            insertion=True,
            schedule_indirection_cache=schedule_indirection_cache,
        )

    amend_subtrees = [
        element
        for element in amending_node.iter()
        if isinstance(element.tag, str) and _localname_of_tag(element.tag) == "amend"
    ]
    if not amend_subtrees:
        # AGENTS §2.5: try the amending-act-own-schedule delegation form.
        delegated = _try_amending_act_own_schedule_delegation(
            amending_node,
            amending_act_root,
            target_leaf_kind=inserted_leaf_kind,
            normalized_label=normalized_label,
            insertion=True,
        )
        if delegated is not None:
            return delegated
        return NZ_STRUCTURAL_INSERT_BLOCKED_NO_AMEND_SUBTREE

    matches = _insertion_leaf_matches(amend_subtrees, inserted_leaf_kind, normalized_label)

    if len(matches) > 1 and normalized_provision:
        # The same inserted-node label is matched in more than one amend subtree
        # (different sections amended by the same amending section). Restrict to
        # the amend subtree(s) whose cited section equals the witness's enclosing
        # section — exact disambiguation by the instruction citation.
        section_scoped = [
            amend
            for amend in amend_subtrees
            if _amend_subtree_section_label(amend) == normalized_provision
        ]
        if section_scoped:
            scoped_matches = _insertion_leaf_matches(section_scoped, inserted_leaf_kind, normalized_label)
            if scoped_matches:
                matches = scoped_matches

    if not matches:
        # AGENTS §2.5: schedule-as-direct-payload delegation retry (see the
        # no-amend-subtree branch above for the same shape).
        delegated = _try_amending_act_own_schedule_delegation(
            amending_node,
            amending_act_root,
            target_leaf_kind=inserted_leaf_kind,
            normalized_label=normalized_label,
            insertion=True,
        )
        if delegated is not None:
            return delegated
        return NZ_STRUCTURAL_INSERT_BLOCKED_NO_MATCHING_CHILD
    if len(matches) > 1:
        return NZ_STRUCTURAL_INSERT_BLOCKED_AMBIGUOUS_MATCH

    # §2.5 audit-state: mirror the replacement path's single-match-wrong-section
    # guard (same AGENTS §1.1 no-silent-target-hijacking discipline).
    if normalized_provision:
        match_section = _amend_subtree_section_label(matches[0])
        if match_section is not None and match_section != normalized_provision:
            return NZ_STRUCTURAL_INSERT_BLOCKED_SINGLE_MATCH_WRONG_SECTION

    inserted_element = matches[0]
    nodes = _walk_payload_root_nodes(inserted_element)
    if not nodes:
        return NZ_STRUCTURAL_INSERT_BLOCKED_EMPTY_PAYLOAD
    root = nodes[0]
    if not root.text.strip():
        return NZ_STRUCTURAL_INSERT_BLOCKED_EMPTY_PAYLOAD
    return NZStructuralReplacement(root=root, descendants=tuple(nodes[1:]))


def _amend_instructions(node: etree._Element) -> tuple[NZAmendInstruction, ...]:
    """Read typed ``<amend.in>``/citation instructions from an amending node.

    Each ``<text>`` descendant that carries one or more ``<amend.in>`` elements
    is one instruction. We classify the prose verb and, for the
    omit/substitute shape with exactly the paired ``<amend.in>`` arity, return
    the exact old/new text and the ``<extref>``/``linkcontent`` target. Every
    other shape (insert, omit-only, structural, odd arity) is still returned as
    a typed instruction with its verb so the consumer can keep it a blocker
    rather than guess. Returns ``()`` when no ``<amend.in>`` is present (the
    common non-amending / schedule-indirection case).
    """
    text_nodes = (
        text_node
        for text_node in node.iter()
        if isinstance(text_node.tag, str) and _localname_of_tag(text_node.tag) == "text"
    )
    return _amend_instructions_from_text_nodes(text_nodes)


def _amend_instructions_from_text_nodes(
    text_nodes: Iterable[etree._Element],
) -> tuple[NZAmendInstruction, ...]:
    """Read typed amendment instructions from preselected ``<text>`` nodes."""

    instructions: list[NZAmendInstruction] = []
    for text_node in text_nodes:
        amend_ins = [
            child
            for child in text_node.iter()
            if isinstance(child.tag, str) and _localname_of_tag(child.tag) == "amend.in"
        ]
        if not amend_ins:
            continue
        flat = _node_text(text_node)
        verb = _amend_instruction_verb(flat)
        target_citation = _amend_instruction_target(text_node)
        each_place = bool(re.search(r"\bin each place\b|\bwherever\b", flat, re.IGNORECASE))
        old_text = ""
        new_text = ""
        anchor_text = ""
        insert_position = ""
        omit_only = False
        if verb in {"omitting_substituting", "replace_with"} and len(amend_ins) == 2:
            old_text = _node_text(amend_ins[0])
            new_text = _node_text(amend_ins[1])
        elif verb == "omitting" and len(amend_ins) == 1:
            # "is amended by omitting <amend.in>X</amend.in>." — a pure deletion
            # of a single span (no substitution). Lowered as a text-replace to
            # the empty string. Multi-``<amend.in>`` omit shapes are left as a
            # not-supported typed instruction (no old/new), never guessed.
            old_text = _node_text(amend_ins[0])
            omit_only = True
        elif verb == "inserting":
            anchor_text, new_text, insert_position = _insert_after_anchor_payload(text_node, amend_ins)
        instructions.append(
            NZAmendInstruction(
                target_citation=target_citation,
                verb=verb,
                old_text=old_text,
                new_text=new_text,
                each_place=each_place,
                anchor_text=anchor_text,
                insert_position=insert_position,
                omit_only=omit_only,
            )
        )
    return tuple(instructions)


def _insert_after_anchor_payload(
    text_node: etree._Element,
    amend_ins: list[etree._Element],
) -> tuple[str, str, str]:
    """Extract (anchor, new_text, position) for an ``inserting`` instruction.

    Only the unambiguous ``<quote.in>``-anchored convention is parsed:
    "amended by inserting, after the word <quote.in>ANCHOR</quote.in>, the words
    <amend.in>NEW</amend.in>". The anchor (existing text the insertion is keyed
    to) lives in ``<quote.in>``; the new text lives in a single ``<amend.in>``.
    ``position`` is ``"after"`` or ``"before"`` read from the prose.

    Returns empty strings (no payload) for any other shape — notably the older
    two-``<amend.in>`` form ("inserting X after Y") whose element order is not a
    reliable anchor/new discriminator — so the consumer keeps it a typed
    not-supported residue rather than guessing which span is the anchor.
    """
    quote_ins = [
        child
        for child in text_node.iter()
        if isinstance(child.tag, str) and _localname_of_tag(child.tag) == "quote.in"
    ]
    if len(quote_ins) != 1 or len(amend_ins) != 1:
        return "", "", ""
    flat = _node_text(text_node).lower()
    if re.search(r"\bafter\b", flat):
        position = "after"
    elif re.search(r"\bbefore\b", flat):
        position = "before"
    else:
        return "", "", ""
    anchor = _node_text(quote_ins[0])
    new_text = _node_text(amend_ins[0])
    if not anchor or not new_text:
        return "", "", ""
    return anchor, new_text, position


def _amend_instruction_verb(flat_text: str) -> str:
    normalized = flat_text.lower()
    has_omit = "omitting" in normalized
    has_subst = "substituting" in normalized
    if has_omit and has_subst:
        return "omitting_substituting"
    # Modern "In <target>, replace <old> with <new>." inline substitution.
    if re.search(r"\breplace\b.*\bwith\b", normalized) and "inserting" not in normalized:
        return "replace_with"
    if "inserting" in normalized:
        return "inserting"
    if has_omit:
        return "omitting"
    if "substituting" in normalized or "replac" in normalized:
        return "substituting"
    return "other"


def _amend_instruction_target(text_node: etree._Element) -> str:
    """Resolve the cited target from a ``<text>`` instruction.

    The target sits in a leading ``<citation>``: modern acts wrap it in an
    ``<extref>``; older consolidated acts wrap it in an ``atidlm:linkcontent``
    inside the citation, prefixed by a bare ``Section``/``Clause`` word. We take
    the first ``<extref>`` text if present, otherwise reconstruct ``<prefix>
    <linkcontent>`` from the first citation. A target inside an ``<amend.in>``
    is payload, not the instruction target, so we ignore citations nested in an
    ``<amend.in>``.
    """
    for descendant in text_node.iter():
        if not isinstance(descendant.tag, str):
            continue
        if _localname_of_tag(descendant.tag) != "extref":
            continue
        if any(
            isinstance(anc.tag, str) and _localname_of_tag(anc.tag) == "amend.in"
            for anc in descendant.iterancestors()
        ):
            continue
        target = _node_text(descendant)
        if target:
            return target
    for descendant in text_node.iter():
        if not isinstance(descendant.tag, str):
            continue
        if _localname_of_tag(descendant.tag) != "citation":
            continue
        if any(
            isinstance(anc.tag, str) and _localname_of_tag(anc.tag) == "amend.in"
            for anc in descendant.iterancestors()
        ):
            continue
        link = ""
        for inner in descendant.iter():
            if isinstance(inner.tag, str) and _localname_of_tag(inner.tag) == "linkcontent":
                link = _node_text(inner)
                break
        if not link:
            continue
        prefix = _normalize_text(descendant.text or "")
        return _normalize_text(f"{prefix} {link}")
    return ""


def _source_zone(xml_path: str) -> str:
    if "/skeletons/" in xml_path:
        return "end_skeleton"
    if "/front/" in xml_path:
        return "front_history"
    if "/end/" in xml_path:
        return "end_history"
    if "/schedule" in xml_path:
        return "primary_schedule"
    if "/body/" in xml_path:
        return "primary_body"
    return "unknown"


def _direct_child_text(node: etree._Element, localname: str) -> str:
    for child in node:
        if not isinstance(child.tag, str):
            continue
        if child.tag == localname or _localname_of_tag(child.tag) == localname:
            if len(child) == 0:
                return _normalize_text(child.text or "")
            return _node_text(child)
    return ""


def _first_descendant_text(node: etree._Element, localname: str) -> str:
    for descendant in node.iter():
        if (
            descendant is not node
            and isinstance(descendant.tag, str)
            and _localname_of_tag(descendant.tag) == localname
        ):
            return _node_text(descendant)
    return ""


def _descendant_texts(node: etree._Element, localname: str) -> Iterable[str]:
    for descendant in node.iter():
        if (
            descendant is not node
            and isinstance(descendant.tag, str)
            and _localname_of_tag(descendant.tag) == localname
        ):
            text = _node_text(descendant)
            if text:
                yield text


def _descendant_attrs(node: etree._Element, localname: str, attr: str) -> Iterable[str]:
    for descendant in node.iter():
        if (
            descendant is not node
            and isinstance(descendant.tag, str)
            and _localname_of_tag(descendant.tag) == localname
        ):
            value = _attr(descendant, attr)
            if value:
                yield value


def _node_text(node: etree._Element) -> str:
    if len(node) == 0:
        return _normalize_text(node.text or "")
    return _normalize_text(" ".join(cast(Iterable[str], node.itertext())))


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    if (
        text.isascii()
        and "\n" not in text
        and "\t" not in text
        and "\r" not in text
        and "\f" not in text
        and "\v" not in text
    ):
        if " " not in text:
            return text
        if text[0] != " " and text[-1] != " " and "  " not in text:
            return text
    return " ".join(text.split())


def _attr(node: etree._Element, key: str) -> str:
    return node.attrib.get(key, "")


@lru_cache(maxsize=None)
def _localname_of_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _localname(value: Any) -> str:
    # EAFP fast path: avoids the ``hasattr`` precheck (a separate C call) and
    # the redundant ``isinstance`` branch on the common lxml-Element path.
    # ``_localname`` is the #1 NZ chain-replay hotspot (~25M calls / 32-version
    # chain); the prior ``hasattr`` / ``isinstance`` cascade cost ~2s of pure
    # call overhead that disappears here. ``.tag`` may be a function (Comment /
    # ProcessingInstruction); fall back to ``str(...)`` in that case to preserve
    # the historical "<cyfunction Comment>" localname behaviour for non-string
    # tags.
    try:
        tag = value.tag
    except AttributeError:
        tag = value
    return _localname_of_tag(tag if isinstance(tag, str) else str(tag))


def main(args: Any) -> None:
    if args.work_id:
        document = parse_archived_work_latest(Path(args.db), args.work_id)
    else:
        archive = open_farchive(Path(args.db))
        try:
            data = archive.get(args.xml_locator)
        finally:
            archive.close()
        if data is None:
            raise SystemExit(f"ERROR: XML locator not archived: {args.xml_locator}")
        document = parse_nz_source_document(data, xml_locator=args.xml_locator, version_id=args.version_id or "")

    if args.json:
        print(json.dumps(document.to_jsonable(include_nodes=not args.summary_only), ensure_ascii=False, indent=2))
        return

    summary = document.summary()
    print(
        f"title={summary['title']!r} version_id={summary['version_id']} "
        f"as_at={summary['as_at']} nodes={summary['nodes']} "
        f"history_witnesses={summary['history_witnesses']} amending_works={summary['amending_works']}"
    )
    print(f"node_kinds={summary['node_kinds']} deleted_nodes={summary['deleted_nodes']}")
    for node in document.nodes[: args.limit]:
        path = "/".join(node.path)
        print(f"{path}\t{node.xml_id}\t{node.heading}\thistory={len(node.history)}")
    if len(document.nodes) > args.limit:
        print(f"... {len(document.nodes) - args.limit} more")
