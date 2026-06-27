"""Label-coverage audit for the Estonia amendment parser (loudness instrument).

This is the EE analog of Finland's token-witness coverage audit
(``finland/johtolause/coverage_audit``).  The Finnish instrument computes, per
amendment johtolause, which operative TOKEN spans the recursive-descent parser
advanced over without producing a node — a silently dropped amendment target.

Estonia's parser (``estonia/peg.extract_ee_ops`` /
``estonia/grafter.parse_ee_amendment_ops``) is regex/char based: it has no token
stream and no per-node source spans, so the Finnish token-witness model cannot be
ported without rebuilding the parser.  Instead this module measures coverage at
LABEL granularity — the EE analog of FI's high-signal ``unmatched_section`` tier:

    A silent drop is an amendment-target LABEL (section / subsection / item) that
    the instruction text NAMES but that NO produced op targets.

Algorithm, per amendment:
  1. MENTIONED labels — regex over each verb-bearing op-item's instruction
     preamble for Estonian reference patterns (``paragrahvi N`` = section,
     ``lõike[s] N`` = subsection, ``punkt[i] N`` = item), handling inflected
     forms, superscript section numbers, and coordinated plurals
     (``lõigetega 4 ja 5`` → {4, 5}).  Numerals are normalised with the SAME
     ``_normalize_num`` the EE parser uses (read-only import from ``peg``), so
     ``12¹`` mentioned matches ``12_1`` produced.
  2. PRODUCED labels — the set of ``(kind, label)`` pairs over every produced
     op's ``target.path`` and ``destination.path``.
  3. SILENT DROP — a mentioned label, at the most-specific named level of its
     op-item, that matches no produced op at that level.  Tiered like FI:
       * ``verb_no_op``        — the op-item carries a recognised amendment verb
         (``_classify_verb`` != ``"unknown"``) but the WHOLE amendment produced
         zero structural ops: the instruction vanished entirely.
       * ``unmatched_section`` — a named section/subsection/item that no op
         targets, in an amendment that did produce some ops.

Only op-items with a recognised amendment verb are scanned for mentioned labels.
This deliberately skips decree (``määrus``) body text, which cites provisions as
a legal basis (``§ 3 lõike 1 alusel ...``) without amending them — those are not
amendment targets and would be pure regex noise.

This is a TRIAGE instrument, NOT an oracle (exactly like the FI audit).  Because
EE ops are not attributed back to individual op-items, a mentioned label is
considered covered if ANY produced op targets it at that level; the unit of the
coverage metric is the verb-bearing op-item.  False positives are acceptable so
long as the reported SHAPES are a useful grammar worklist.  It performs ZERO
parser changes and imports only read-only helpers from ``peg``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass

from lawvm.core.regex_safety import compile_classifier_regex
from lawvm.estonia.peg import (
    _EE_SUPERSCRIPT_DIGIT_CLASS,
    _classify_verb,
    _normalize_num,
    parse_html_op_items,
)
from lawvm.estonia.grafter import _first_tavatekst_text, parse_ee_amendment_ops

# Signal tiers, strongest first — mirrors the FI audit's tier names so both
# parse-bench jurisdictions read the same.
TIER_VERB_NO_OP = "verb_no_op"
TIER_UNMATCHED_SECTION = "unmatched_section"

# A label fragment: digits, optional superscript run, optional lowercase letter
# suffix (e.g. ``71``, ``12¹``, ``5a``).  Normalised via ``_normalize_num``.
_NUM = r"\d+[" + _EE_SUPERSCRIPT_DIGIT_CLASS + r"]*[a-zõäöü]?"

# A coordinated numeral group: ``4``, ``4 ja 5``, ``4, 5 ja 6``, ``4 ning 5``.
_NUM_GROUP = _NUM + r"(?:\s*(?:,|ja|ning)\s*" + _NUM + r")*"

# Section: ``paragrahvi 16``, ``paragrahvis 7``, ``§ 12``.  The ``§`` form is the
# old-style symbol; ``paragrahv`` is the inflected word form.
_SECTION_RE = re.compile(
    r"(?:paragrahv(?:i|is|it|ist|iga|ile|ide|ides|idesse|idest)?|§)\s*"
    r"(" + _NUM_GROUP + r")",
    re.IGNORECASE,
)
# Subsection (lõige).  Nominative ``lõige`` uses the ``lõige``/``lõigete`` stem;
# the common genitive/inessive forms use the ``lõik(e)`` stem (``lõike 1`` =
# "of subsection 1", ``lõikes 3`` = "in subsection 3", ``lõiget 4``).  Both stems
# plus coordinated plurals ``lõigetega 4 ja 5`` / ``lõiked 1 ja 2`` are covered.
_SUBSECTION_RE = re.compile(
    r"l[õo]i"
    r"(?:ge(?:te(?:ga|sse|st)?|ga|d|s|sse|st|t)?"  # lõige / lõigete... / lõiked
    r"|ke(?:s|sse|st|ga)?|ked|kele|kes)"  # lõike / lõikes / lõiked ...
    r"\s+(" + _NUM_GROUP + r")",
    re.IGNORECASE,
)
# Item (punkt): ``punkt 5``, ``punkti 2``, ``punktis 3``, ``punktid 1 ja 2``.
_ITEM_RE = re.compile(
    r"punkt(?:i|is|ist|iks|id|ide|idega|e)?\s+(" + _NUM_GROUP + r")",
    re.IGNORECASE,
)

# Level ordering, most-specific last.
_SECTION = "section"
_SUBSECTION = "subsection"
_ITEM = "item"
_LEVEL_DEPTH = {_SECTION: 0, _SUBSECTION: 1, _ITEM: 2}

# Citation / legal-basis context.  A provision named in such a frame is a
# REFERENCE (the basis on which the decree body acts, or an external EU
# instrument cited inside a definition), NOT an amendment target.  Decree
# (``määrus``) consolidations are full of these — ``§ 22 lõike 2 punkti 8
# alusel lisatakse taotlusele ...`` ("on the basis of ... is added to the
# application") uses an amendment-looking verb (``lisatakse``) operationally,
# not to amend.  Op-items whose instruction preamble matches this are excluded
# from the verb-item universe so they cannot generate regex-noise drops.  This
# is the EE analog of FI's ``leading_preamble`` exclusion.
_CITATION_CONTEXT_RE = compile_classifier_regex(
    r"\b("
    r"alusel"  # "on the basis of <provision>"
    r"|nimetatu[a-zõäöü]*"  # "referred to" (nimetatud / nimetatuga / nimetatule)
    r"|tähenduses"  # "in the sense of <provision>"
    r"|kohaselt"  # "in accordance with <provision>"
    r"|s[äa]testatud"  # "laid down in <provision>"
    r"|viidatud"  # "referred to in <provision>"
    r"|m[äa][äa]ruse\s*\(E[LÜ]"  # external EU regulation citation
    r"|direktiivi"  # external EU directive citation
    r"|Euroopa\s+Parlamendi"  # "of the European Parliament ..." (EU instrument)
    r")\b",
    re.IGNORECASE,
    classifier_id="ee.coverage_audit.citation_context_re",
)


def _is_citation_context(preamble: str) -> bool:
    """True when the instruction preamble names provisions only as references."""
    return bool(_CITATION_CONTEXT_RE.search(preamble))


@dataclass(frozen=True)
class EeLabelDrop:
    """A mentioned amendment-target label that no produced op covers.

    Attributes:
        tier:         ``verb_no_op`` or ``unmatched_section``.
        level:        ``section`` / ``subsection`` / ``item`` — the level of the
                      mentioned-but-unmatched label.
        label:        the normalised label text (e.g. ``16``, ``12_1``).
        verb:         the recognised amendment verb of the op-item, for context.
        item_text:    the verbatim op-item instruction (self-evidencing).
        shape:        the level-tuple of the item's mentioned address (worklist
                      shape signature), e.g. ``("section", "subsection", "item")``.
    """

    tier: str
    level: str
    label: str
    verb: str
    item_text: str
    shape: tuple[str, ...]


@dataclass(frozen=True)
class EeAmendmentCoverage:
    """Per-amendment coverage result over its verb-bearing op-items."""

    sid: str
    n_ops: int
    n_verb_items: int
    n_clean_items: int  # verb-items with no unmatched mentioned target
    drops: tuple[EeLabelDrop, ...]


def _expand_group(raw: str) -> list[str]:
    """Split a coordinated numeral group into normalised labels.

    ``"4 ja 5"`` → ``["4", "5"]``; ``"12¹"`` → ``["12_1"]``.
    """
    parts = re.split(r"\s*(?:,|\bja\b|\bning\b)\s*", raw, flags=re.IGNORECASE)
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        norm = _normalize_num(part)
        if norm:
            out.append(norm)
    return out


def _labels_from_preamble(preamble: str) -> dict[str, list[str]]:
    """Return ``{level: [normalised labels]}`` from an already-sliced preamble."""
    out: dict[str, list[str]] = {}
    for level, regex in (
        (_SECTION, _SECTION_RE),
        (_SUBSECTION, _SUBSECTION_RE),
        (_ITEM, _ITEM_RE),
    ):
        labels: list[str] = []
        for m in regex.finditer(preamble):
            labels.extend(_expand_group(m.group(1)))
        if labels:
            # Preserve order, drop duplicates.
            seen: set[str] = set()
            uniq = [x for x in labels if not (x in seen or seen.add(x))]
            out[level] = uniq
    return out


def mentioned_labels(item_text: str) -> dict[str, list[str]]:
    """Return ``{level: [normalised labels]}`` mentioned in one op-item.

    Scans the instruction preamble only — the slice BEFORE the quoted payload —
    so that section references inside the new (quoted) text are not counted as
    amendment targets of THIS op.
    """
    from lawvm.estonia.peg import _instruction_preamble

    return _labels_from_preamble(_instruction_preamble(item_text))


def produced_labels(ops: Sequence[object]) -> dict[str, set[str]]:
    """Return ``{level: {labels}}`` over every produced op's target/destination.

    Covers ``target.path`` and ``destination.path``; each path element is a
    ``(kind, label)`` pair with ``kind`` in ``section``/``subsection``/``item``.
    """
    out: dict[str, set[str]] = {_SECTION: set(), _SUBSECTION: set(), _ITEM: set()}
    for op in ops:
        for addr_attr in ("target", "destination"):
            addr = getattr(op, addr_attr, None)
            path = getattr(addr, "path", None)
            if not path:
                continue
            for kind, label in path:
                if kind in out and label:
                    out[kind].add(str(label))
    return out


def _most_specific_level(mentioned: dict[str, list[str]]) -> str | None:
    """Return the deepest level present in a mentioned-label map."""
    present = [lvl for lvl in mentioned if mentioned[lvl]]
    if not present:
        return None
    return max(present, key=lambda lvl: _LEVEL_DEPTH[lvl])


def audit_amendment_labels(
    op_item_texts: Sequence[str],
    ops: Sequence[object],
    *,
    sid: str = "",
) -> EeAmendmentCoverage:
    """Compute label coverage for one amendment.

    ``op_item_texts`` are the raw instruction surfaces (from
    ``parse_html_op_items`` + first-tavatekst), ``ops`` the produced
    ``LegalOperation`` list.  Only items with a recognised amendment verb are
    scanned; each contributes one unit to the coverage metric.
    """
    from lawvm.estonia.peg import _instruction_preamble

    produced = produced_labels(ops)
    has_any_op = any(produced[lvl] for lvl in produced)

    # Verb-bearing instruction items, EXCLUDING decree-body / EU-citation frames
    # (their named provisions are references, not amendment targets — see
    # ``_is_citation_context``).  Each surviving item is one coverage unit.
    verb_items: list[tuple[str, str, str]] = []  # (text, verb, preamble)
    for text in op_item_texts:
        verb = _classify_verb(text)
        if verb == "unknown":
            continue
        preamble = _instruction_preamble(text)
        if _is_citation_context(preamble):
            continue
        verb_items.append((text, verb, preamble))

    drops: list[EeLabelDrop] = []
    clean = 0
    for text, verb, preamble in verb_items:
        mentioned = _labels_from_preamble(preamble)
        level = _most_specific_level(mentioned)
        if level is None:
            # Verb recognised but no named target (e.g. whole-act rename) —
            # not a label-level drop; count it as clean for the label metric.
            clean += 1
            continue

        shape = tuple(
            lvl for lvl in (_SECTION, _SUBSECTION, _ITEM) if mentioned.get(lvl)
        )
        unmatched = [
            lbl for lbl in mentioned[level] if lbl not in produced[level]
        ]
        if not unmatched:
            clean += 1
            continue

        # The whole amendment produced no ops at all -> the verbed instruction
        # vanished entirely (highest signal).  Otherwise it is a specific named
        # target that no op covers.
        tier = TIER_VERB_NO_OP if not has_any_op else TIER_UNMATCHED_SECTION
        for lbl in unmatched:
            drops.append(
                EeLabelDrop(
                    tier=tier,
                    level=level,
                    label=lbl,
                    verb=verb,
                    item_text=text,
                    shape=shape,
                )
            )

    return EeAmendmentCoverage(
        sid=sid,
        n_ops=len(ops),
        n_verb_items=len(verb_items),
        n_clean_items=clean,
        drops=tuple(drops),
    )


def _xml_ns(root: "ET.Element[str]") -> str:
    return root.tag.split("}")[0].strip("{")


def extract_op_item_texts(xml_bytes: bytes) -> list[str]:
    """Collect the amendment instruction surfaces from an RT amendment XML.

    Mirrors ``estonia/replay.py``: per ``paragrahv``, the first ``tavatekst``
    (pre-2009 CDATA path) plus every ``HTMLKonteiner`` split into numbered
    op-items and every standalone ``tavatekst`` block.  De-duplicated, preserving
    order, because RT documents repeat the same op text across version layers.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    ns = _xml_ns(root)
    out: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        text = text.strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    for para in root.iter(f"{{{ns}}}paragrahv"):
        first_tava = _first_tavatekst_text(para, ns)
        if first_tava:
            _add(first_tava)
        for st in para.iter(f"{{{ns}}}sisuTekst"):
            for hk in st.findall(f"{{{ns}}}HTMLKonteiner"):
                for item in parse_html_op_items(hk.text or ""):
                    _add(item)
            for t in st.findall(f"{{{ns}}}tavatekst"):
                txt = " ".join(str(x) for x in t.itertext()).replace("\xa0", " ")
                txt = re.sub(r"\s+", " ", txt).strip()
                _add(txt)
    return out


def audit_amendment_xml(xml_bytes: bytes, *, sid: str = "") -> EeAmendmentCoverage:
    """Parse + audit one amendment XML document.  Convenience entry point."""
    ops = parse_ee_amendment_ops(xml_bytes, source_id=f"ee/{sid}" if sid else "")
    items = extract_op_item_texts(xml_bytes)
    return audit_amendment_labels(items, list(ops), sid=sid)
