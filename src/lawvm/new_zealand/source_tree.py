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
from pathlib import Path
from typing import Any, Iterable, Mapping

from lxml import etree

from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dates import nz_date_text_to_iso
from lawvm.new_zealand.dependencies import latest_xml_locator_for_work, parse_public_act_citation


_STRUCTURAL_TAGS = {"def-para", "label-para", "part", "prov", "schedule", "subprov"}
_TEXT_EXCLUDE_TAGS = {"notes", "history", "history-note"}

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

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "target_citation": self.target_citation,
            "verb": self.verb,
            "old_text": self.old_text,
            "new_text": self.new_text,
            "each_place": self.each_place,
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
    root = etree.fromstring(xml_bytes)
    metadata = _document_metadata(root)
    nodes: list[NZSourceNode] = []
    document_history: list[NZHistoryWitness] = []
    attached_history_note_keys: set[str] = set()

    for child in root:
        _walk_source_nodes(
            child,
            path=(),
            nodes=nodes,
            attached_history_note_keys=attached_history_note_keys,
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
) -> None:
    if not isinstance(node.tag, str):
        return
    kind = _localname(node)
    if kind in _STRUCTURAL_TAGS:
        if kind == "def-para":
            label = _first_def_term(node)
        else:
            label = _direct_child_text(node, "label")
        segment = _path_segment(kind, label, _attr(node, "id"), len(nodes) + 1)
        current_path = (*path, segment)
        history_notes = tuple(_direct_history_notes(node))
        attached_history_note_keys.update(_element_source_key(note) for note in history_notes)
        source_node = NZSourceNode(
            kind=kind,
            path=current_path,
            xml_id=_attr(node, "id"),
            xml_path=_element_source_key(node),
            source_zone=_source_zone(_element_source_key(node)),
            label=label,
            heading=_direct_child_text(node, "heading"),
            deletion_status=_attr(node, "deletion-status"),
            text=_legal_text(node),
            history=tuple(_history_witness(note) for note in history_notes),
            amend_instructions=_amend_instructions(node),
        )
        nodes.append(source_node)
        for child in node:
            _walk_source_nodes(
                child,
                path=current_path,
                nodes=nodes,
                attached_history_note_keys=attached_history_note_keys,
            )
        return
    for child in node:
        _walk_source_nodes(
            child,
            path=path,
            nodes=nodes,
            attached_history_note_keys=attached_history_note_keys,
        )


def _document_metadata(root: etree._Element) -> dict[str, str]:
    metadata: dict[str, str] = {
        _localname(key): value
        for key, value in root.attrib.items()
    }
    title = ""
    for node in root.iter():
        if isinstance(node.tag, str) and _localname(node) == "title":
            title = _node_text(node)
            break
    if title:
        metadata["title"] = title
    metadata["root_tag"] = _localname(root)
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
        if isinstance(descendant.tag, str) and _localname(descendant) == _DEF_TERM_TAG:
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
        if _localname(child) == "notes":
            for descendant in child.iter():
                if isinstance(descendant.tag, str) and _localname(descendant) == "history-note":
                    yield descendant
        elif _localname(child) == "history-note":
            yield child


def _iter_localname(root: etree._Element, localname: str) -> Iterable[etree._Element]:
    for node in root.iter():
        if isinstance(node.tag, str) and _localname(node) == localname:
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
    return NZHistoryWitness(
        xml_id=_attr(node, "id"),
        xml_path=_element_source_key(node),
        text=text,
        amended_provision=_first_descendant_text(node, "amended-provision"),
        operation=_first_descendant_text(node, "amending-operation"),
        amendment_date=amendment_date,
        amendment_date_iso=nz_date_text_to_iso(amendment_date),
        amending_provisions=tuple(_descendant_texts(node, "amending-provision")),
        amending_provision_hrefs=tuple(_descendant_attrs(node, "amending-provision", "href")),
        amending_legislation=_first_descendant_text(node, "amending-leg"),
        amending_work_id=work_id,
        defined_term=_history_note_defined_term(node),
    )


def _history_note_defined_term(node: etree._Element) -> str:
    """Extract the defined term a definition-level history note targets.

    NZ writes definition notes as ``<amended-provision>Section 2(1)</amended-
    provision> <emphasis style="bold">term</emphasis>: <amending-operation>
    repealed</amending-operation>, ...``. The defined term is the bold
    ``emphasis`` that is a direct child of the note. We require the emphasis to
    sit between the ``amended-provision`` and the ``amending-operation`` so an
    incidental emphasis elsewhere in the note text is not mistaken for a term.
    Terms carrying the path separators ``/`` or ``:`` are dropped (cannot be a
    clean addressable label).
    """
    saw_amended_provision = False
    for child in node:
        if not isinstance(child.tag, str):
            continue
        local = _localname(child)
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


def _legal_text(node: etree._Element) -> str:
    texts: list[str] = []
    for descendant in node.iter():
        if descendant is node:
            continue
        if not isinstance(descendant.tag, str):
            continue
        if _localname(descendant) in _TEXT_EXCLUDE_TAGS:
            continue
        if any(_localname(parent) in _TEXT_EXCLUDE_TAGS for parent in descendant.iterancestors()):
            continue
        if descendant.text:
            texts.append(descendant.text)
        if descendant.tail:
            texts.append(descendant.tail)
    return _normalize_text(" ".join(texts))


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
    instructions: list[NZAmendInstruction] = []
    for text_node in node.iter():
        if not isinstance(text_node.tag, str) or _localname(text_node) != "text":
            continue
        amend_ins = [child for child in text_node.iter() if isinstance(child.tag, str) and _localname(child) == "amend.in"]
        if not amend_ins:
            continue
        flat = _node_text(text_node)
        verb = _amend_instruction_verb(flat)
        target_citation = _amend_instruction_target(text_node)
        each_place = bool(re.search(r"\bin each place\b|\bwherever\b", flat, re.IGNORECASE))
        if verb in {"omitting_substituting", "replace_with"} and len(amend_ins) == 2:
            old_text = _node_text(amend_ins[0])
            new_text = _node_text(amend_ins[1])
        else:
            old_text = ""
            new_text = ""
        instructions.append(
            NZAmendInstruction(
                target_citation=target_citation,
                verb=verb,
                old_text=old_text,
                new_text=new_text,
                each_place=each_place,
            )
        )
    return tuple(instructions)


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
        if _localname(descendant) != "extref":
            continue
        if any(_localname(anc) == "amend.in" for anc in descendant.iterancestors()):
            continue
        target = _node_text(descendant)
        if target:
            return target
    for descendant in text_node.iter():
        if not isinstance(descendant.tag, str):
            continue
        if _localname(descendant) != "citation":
            continue
        if any(_localname(anc) == "amend.in" for anc in descendant.iterancestors()):
            continue
        link = ""
        for inner in descendant.iter():
            if isinstance(inner.tag, str) and _localname(inner) == "linkcontent":
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
        if isinstance(child.tag, str) and _localname(child) == localname:
            return _node_text(child)
    return ""


def _first_descendant_text(node: etree._Element, localname: str) -> str:
    for descendant in node.iter():
        if descendant is not node and isinstance(descendant.tag, str) and _localname(descendant) == localname:
            return _node_text(descendant)
    return ""


def _descendant_texts(node: etree._Element, localname: str) -> Iterable[str]:
    for descendant in node.iter():
        if descendant is not node and isinstance(descendant.tag, str) and _localname(descendant) == localname:
            text = _node_text(descendant)
            if text:
                yield text


def _descendant_attrs(node: etree._Element, localname: str, attr: str) -> Iterable[str]:
    for descendant in node.iter():
        if descendant is not node and isinstance(descendant.tag, str) and _localname(descendant) == localname:
            value = _attr(descendant, attr)
            if value:
                yield value


def _node_text(node: etree._Element) -> str:
    return _normalize_text(" ".join(str(part) for part in node.itertext()))


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _attr(node: etree._Element, key: str) -> str:
    return node.attrib.get(key, "")


def _localname(value: Any) -> str:
    if hasattr(value, "tag"):
        value = value.tag
    if isinstance(value, str):
        return value.rsplit("}", 1)[-1]
    return str(value).rsplit("}", 1)[-1]


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
