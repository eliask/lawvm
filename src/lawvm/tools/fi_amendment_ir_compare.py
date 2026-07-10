"""``lawvm fi-amendment-ir-compare`` — amendment IR-EQUIVALENCE eval (PDF→IR vs XML→IR).

LawVM's product is the legal IR (amendment *operations* → consolidation), not PDF
text.  The real accuracy target is therefore NOT "does the reconstructed PDF text
look like the XML" (that is :mod:`lawvm.tools.fi_parse_compare`) but the stronger,
product-level question:

    does the PDF→IR path produce the SAME amendment operations as the trusted
    XML→IR path?

Both witnesses are fed through the *identical* clause parser
(:func:`lawvm.finland.johtolause.api.parse_clause`), so the op-level diff isolates
PDF-text faithfulness at the OPERATION level — a dropped section, a misread §
number, or a REPLACE read as an INSERT shows up as a typed divergence, while a
harmless whitespace/hyphenation difference does not (it lowers to the identical
op).

    XML side (trusted reference):
        main.xml  --get_johtolause-->  enacting-clause text  --parse_clause-->  ClauseAST
    PDF side  (path under test):
        gazette PDF  --vision reading text-->  operative-clause text  --parse_clause-->  ClauseAST

    diff_amendment_ops(xml_ClauseAST, pdf_ClauseAST) -> tuple[OpDivergence, ...]

This is an EXACTNESS eval, not a fuzzy benchmark.  There is no word-coverage /
WER / numeric-recall similarity score in the headline: every op is either
EXACTLY matched (same target address + same op kind) or a TYPED divergence, and
the top-level result is a stream of ``OpDivergence`` records plus counts.  The
result PASSes iff there are zero typed divergences.

``OpDivergence`` is the STABLE return contract consumed by the T1 local-LLM
adjudicator + terminal image-escalation queue.  Its dataclass SHAPE is frozen
(five fields); only the ``kind`` vocabulary grows:

    OpDivergence(kind, target_ref, xml_op, pdf_op, detail)
        kind       one of DIVERGENCE_KINDS (see below)
        target_ref  the canonical §/chapter/moment/kohta reference the ops share
        xml_op      rendered XML op ("<action> <target_ref>"), or None
        pdf_op      rendered PDF op, or None
        detail      human-readable note (carries the escalation locator)

Divergence vocabulary (typed, mirrors LawVM oracle-touch/verdict machinery):

    matched            structurally equal op (same target address + same kind)
    op_missing_in_pdf  XML has an op the PDF IR does not (PDF dropped it)
    op_extra_in_pdf    PDF IR has an op the XML does not (PDF hallucinated it)
    kind_mismatch      SAME target address, DIFFERENT op kind (e.g. REPLACE↔INSERT)
    payload_mismatch   SAME matched op, but the replacement BODY TEXT differs
                       between witnesses beyond the inert-encoding quotient
                       (:mod:`lawvm.finland.op_equivalence`).  Emitted by the
                       downstream PAYLOAD stage (:func:`diff_op_payloads`), which
                       binds each matched op's target to its body unit on BOTH
                       witnesses and runs ``text_equivalence`` over the two body
                       texts.  REPEAL ops carry no payload (tombstone — skipped);
                       a target whose body is not inventoried/segmentable on one
                       witness (thin XML frame, scanned-thin PDF) is TYPE-DEFERRED
                       (counted, never forced into a spurious payload_mismatch).

    (reserved, NOT emitted yet: ``target_mismatch`` — the johtolause op only
     names WHICH provision and HOW; a genuine target divergence surfaces as an
     op_missing/op_extra pair at the op-structure stage.)

Benign terminal strata (typed status on ``CompareResult``, never a silent empty):

  * ``xml_frame_only`` — a TABLE amendment carries a THIN main.xml (just the
    entry-into-force frame, e.g. ``sd/2003/917`` ≈ 290 chars) while its PDF holds
    the real tables → XML→ops is frame-only, so this is a PDF-ONLY case,
    terminal-adjudicated (:class:`XmlIncompleteError`).  We do NOT force a diff.
  * ``pdf_annex_only`` — in this archive the modern ``sd/<y>/<n>/fin/media/*.pdf``
    is the ANNEX attachment (liite), NOT the operative gazette; its reading text
    carries no johtolause → PDF→ops is empty (:class:`OperativeClauseNotFound`).
    The genuine end-to-end case is an OLDER scanned gazette whose media PDF IS the
    full statute page (vision-read).

Substrate-adequacy is a cheap DIAGNOSTIC only (did the PDF text even yield an
extractable operative clause) — it is never the headline; the headline is the
exact op-set diff.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Optional, Union

if TYPE_CHECKING:
    from lawvm.finland.body_pairing import ObservedBodyUnit

from lawvm.core.clause_ast import (
    ClauseAST,
    ClauseNode,
    LabelAmend,
    RefAmend,
    ScopedBlock,
)
from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.finland.op_equivalence import text_equivalence

_FINLEX_FARCHIVE = "data/finlex.farchive"

#: Below this the main.xml body is a thin table-frame (entry-into-force only); the
#: XML→ops path is structurally near-empty and a comparison would be meaningless.
_XML_BODY_MIN_CHARS = 2000

# The enacting johtolause is bounded by an operative verb (passive present OR the
# historical past-participle ministry-decision form) and the terminal
# "... seuraavasti:".  We accept both verb families so a scanned 1990s ministry
# päätös ("... on kumonnut ... muuttanut ... lisännyt ... seuraavasti:") reads the
# same clause the XML get_johtolause returns for it.
_OPERATIVE_VERB_RE = re.compile(
    r"\b("
    r"kumo(?:taan|nnut|ttu)|"
    r"muut(?:etaan|tanut|ettu)|"
    r"lis[äa](?:t[äa]{1,2}n|[äa]nnyt|[äa]tty)|"
    r"s[äa]{1,2}det[äa]{1,2}n|"
    r"poist(?:etaan|anut|ettu)|"
    r"siirr(?:et[äa]{1,2}n|[äa]nyt|etty)|"
    r"korv(?:ataan|annut|attu)"
    r")\b",
    re.IGNORECASE,
)
_SEURAAVASTI_RE = re.compile(r"seuraavasti", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Typed failures                                                              #
# --------------------------------------------------------------------------- #


class AmendmentIrCompareError(Exception):
    """Base for all typed failures of the amendment-IR comparison."""


class XmlIncompleteError(AmendmentIrCompareError):
    """The main.xml body is a thin table-frame (``xml_incomplete``).

    XML→ops is structurally near-empty (the real content is PDF-only tables); a
    comparison would be a forced apples-to-oranges diff, so we refuse it.
    """


class OperativeClauseNotFound(AmendmentIrCompareError):
    """No operative johtolause ("... seuraavasti:") was locatable in the text.

    On the PDF side this is the ``pdf_incomplete`` stratum: the media PDF is an
    annex attachment, not the operative gazette page.
    """


class ClauseParseFailed(AmendmentIrCompareError):
    """``parse_clause`` crashed (resolver/lowerer error) on the operative text."""


# --------------------------------------------------------------------------- #
# Flattened op model + op-level divergence (the STABLE consumer contract)     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FlatOp:
    """One structural amendment operation, flattened out of a ``ClauseAST``.

    ``target_ref`` is the canonical rendering of the op's ``LegalAddress``
    (``chapter:4/section:5/subsection:4`` [``/heading``]).  It is the matching key
    the op-diff pairs XML and PDF ops on.  ``action`` is the lowered verb
    (``replace`` / ``repeal`` / ``insert`` / ``heading_replace`` / ``renumber``).
    """

    action: str
    target_ref: str

    @property
    def render(self) -> str:
        return f"{self.action} {self.target_ref}"


# The typed divergence vocabulary (mirrors LawVM oracle-touch/verdict machinery).
# The first four are emitted by the OP-STRUCTURE stage (``diff_amendment_ops``);
# ``payload_mismatch`` is emitted by the PAYLOAD stage (``diff_op_payloads``),
# which compares the matched op's replacement BODY TEXT across witnesses modulo
# the inert-encoding quotient. ``target_mismatch`` stays reserved (see docstring).
DIVERGENCE_KINDS = (
    "matched",
    "op_missing_in_pdf",
    "op_extra_in_pdf",
    "kind_mismatch",
    "payload_mismatch",
)

#: A comparison is a clean pass iff every divergence is "matched".
_BENIGN_MATCH = "matched"


@dataclass(frozen=True, slots=True)
class OpDivergence:
    """One op-level divergence between the XML (reference) and PDF (under-test) IR.

    STABLE CONTRACT — consumed by the T1 local-LLM adjudicator + terminal
    image-escalation queue.  Five-field shape is frozen; ``kind`` ∈
    :data:`DIVERGENCE_KINDS`:

    kind:       "matched"           — same target address, same op kind (exact)
                "op_missing_in_pdf" — XML has an op the PDF IR does not (dropped)
                "op_extra_in_pdf"   — PDF IR has an op the XML does not (extra)
                "kind_mismatch"     — same target address, DIFFERENT op kind
    target_ref: the §/chapter/moment/kohta reference the ops share (escalation loc)
    xml_op:     rendered XML op ("<action> <target_ref>"), or None
    pdf_op:     rendered PDF op, or None
    detail:     human-readable note
    """

    kind: str
    target_ref: str
    xml_op: Optional[str]
    pdf_op: Optional[str]
    detail: str


_OpsInput = Union[ClauseAST, "tuple[FlatOp, ...]"]


def flatten_clause_ast(ast: ClauseAST) -> tuple[FlatOp, ...]:
    """Flatten a ``ClauseAST`` to its structural ``FlatOp`` list (reading order).

    Walks every ``VerbGroup`` and recurses through ``ScopedBlock`` grouping nodes.
    Only ``RefAmend`` and ``LabelAmend`` carry a target+action and become ops;
    ``TextAmend`` / meta / renumber-tail nodes are skipped (they do not name a
    structural §/chapter/moment target and so cannot be matched op-to-op).
    """
    ops: list[FlatOp] = []

    def _emit(node: ClauseNode) -> None:
        if isinstance(node, ScopedBlock):
            for child in node.children:
                _emit(child)
            return
        if isinstance(node, (RefAmend, LabelAmend)):
            target = node.target
            if not target.path:
                return
            action = node.action.value if hasattr(node.action, "value") else str(node.action)
            ops.append(FlatOp(action=str(action), target_ref=str(target)))

    for vg in ast.verb_groups:
        for node in vg.nodes:
            _emit(node)
    return tuple(ops)


def _as_flat(ops: _OpsInput) -> tuple[FlatOp, ...]:
    if isinstance(ops, ClauseAST):
        return flatten_clause_ast(ops)
    return tuple(ops)


def diff_amendment_ops(xml_ops: _OpsInput, pdf_ops: _OpsInput) -> tuple[OpDivergence, ...]:
    """Op-level diff of the XML (reference) IR against the PDF (under-test) IR.

    Accepts a ``ClauseAST`` (flattened here) or an already-flattened
    ``tuple[FlatOp, ...]`` on either side.  Ops are matched by ``target_ref``; a
    matched pair with the same action is ``matched``, with a different action is
    ``changed``.  A target only on the XML side is ``pdf_missing``; only on the
    PDF side is ``pdf_extra``.  Ordering is deterministic: XML reading order
    first, then PDF-only ops in PDF reading order.
    """
    xml_flat = _as_flat(xml_ops)
    pdf_flat = _as_flat(pdf_ops)

    # First-wins index by target_ref (duplicate refs within one witness are rare;
    # note them rather than silently collapsing).
    def _index(flat: tuple[FlatOp, ...]) -> "dict[str, FlatOp]":
        idx: dict[str, FlatOp] = {}
        for op in flat:
            idx.setdefault(op.target_ref, op)
        return idx

    xml_idx = _index(xml_flat)
    pdf_idx = _index(pdf_flat)

    out: list[OpDivergence] = []
    seen: set[str] = set()

    for op in xml_flat:
        ref = op.target_ref
        if ref in seen:
            continue
        seen.add(ref)
        xml_op = xml_idx[ref]
        pdf_op = pdf_idx.get(ref)
        if pdf_op is None:
            out.append(
                OpDivergence(
                    kind="op_missing_in_pdf",
                    target_ref=ref,
                    xml_op=xml_op.render,
                    pdf_op=None,
                    detail="op present in XML IR, absent from PDF IR",
                )
            )
        elif pdf_op.action == xml_op.action:
            out.append(
                OpDivergence(
                    kind="matched",
                    target_ref=ref,
                    xml_op=xml_op.render,
                    pdf_op=pdf_op.render,
                    detail="",
                )
            )
        else:
            out.append(
                OpDivergence(
                    kind="kind_mismatch",
                    target_ref=ref,
                    xml_op=xml_op.render,
                    pdf_op=pdf_op.render,
                    detail=f"same target, op kind differs: xml={xml_op.action} pdf={pdf_op.action}",
                )
            )

    for op in pdf_flat:
        ref = op.target_ref
        if ref in seen:
            continue
        seen.add(ref)
        out.append(
            OpDivergence(
                kind="op_extra_in_pdf",
                target_ref=ref,
                xml_op=None,
                pdf_op=op.render,
                detail="op present in PDF IR, absent from XML IR",
            )
        )

    return tuple(out)


# --------------------------------------------------------------------------- #
# Payload stage — body-text equivalence for MATCHED ops                       #
# --------------------------------------------------------------------------- #
#
# The op-structure diff above proves both witnesses name the SAME provision +
# the SAME verb. The payload stage proves the NEW BODY TEXT they carry for that
# provision is the same too, modulo the legally-inert encoding quotient owned by
# ``lawvm.finland.op_equivalence.text_equivalence`` (Cf-format, soft-hyphen line
# joins, whitespace). A residual that survives every inert fold is a genuine
# body divergence → ``payload_mismatch``.
#
# The replacement text lives in the amending statute BODY (the muutos section
# bodies), NOT the johtolause — so we pair each matched op's target to its body
# unit on BOTH witnesses and compare those two body texts. A target whose body is
# absent on either witness (thin XML table-frame; scanned-thin PDF reconstruction)
# is TYPE-DEFERRED — counted, never forced into a spurious payload_mismatch.

#: Leading "N §" section-number header (the target address, already matched at the
#: op-structure stage) — stripped from both witnesses so the payload comparison is
#: over the PROSE, not the address glyphs (whose spacing differs by witness).
_LEADING_SECTION_HEADER_RE = re.compile(r"^\s{0,4}\d{1,4}\s{0,3}[a-zä]?\s{0,3}§\s*", re.IGNORECASE)

#: A body-section header inside PDF reading text ("7 §", "2 a §"): a number, an
#: optional single letter, then the section sign. Used to segment the post-
#: johtolause reading text into per-target body payloads. The ``(?!\s{0,2}:)``
#: guard rejects a case-INFLECTED in-body cross-reference ("4 §:n 1 kohta", "3 §:ssä")
#: — the case colon marks a reference, never a standalone body header — so a
#: cross-reference inside one section's body does not spuriously truncate it.
_PDF_BODY_SECTION_RE = re.compile(r"(\d{1,4}\s{0,3}[a-zä]?)\s{0,3}§(?!\s{0,2}:)", re.IGNORECASE)

#: Detail-string trim width for the residual left/right canon carried to adjudication.
_PAYLOAD_CANON_TRIM = 80


def _unit_target_ref(unit: "ObservedBodyUnit") -> str:
    """Render an ``ObservedBodyUnit`` to an op ``target_ref`` key.

    Mirrors :meth:`LegalAddress.__str__` so a body unit and the matched op that
    claims it share a lookup key (``section:7``, ``chapter:4/section:5``,
    ``chapter:2``). Only section/chapter/part units are keyed (the payload-
    comparable strata); anything else returns "".
    """
    parts: list[str] = []
    if unit.part_label:
        parts.append(f"part:{unit.part_label}")
    if unit.chapter_label:
        parts.append(f"chapter:{unit.chapter_label}")
    if unit.kind in ("section", "chapter", "part"):
        parts.append(f"{unit.kind}:{unit.label}")
    else:
        return ""
    return "/".join(parts)


def _xml_body_payloads(xml_data: bytes) -> "dict[str, str]":
    """Map ``target_ref`` → replacement body text from a main.xml amendment body.

    Uses the sanctioned body inventory (:func:`build_observed_body_inventory`),
    so the payload text is exactly what the FI replay pairing lane sees. First-
    wins on a duplicate key (rare; see the inventory's own ``#N`` disambiguation).
    """
    from lawvm.core.xml_parse import parse_corpus_xml
    from lawvm.finland.body_pairing import build_observed_body_inventory

    try:
        root = parse_corpus_xml(xml_data)
    except Exception:
        return {}
    out: dict[str, str] = {}
    for unit in build_observed_body_inventory(root):
        ref = _unit_target_ref(unit)
        if ref and ref not in out:
            out[ref] = unit.source_text
    return out


def _pdf_body_payloads(reading_text: str) -> "dict[str, str]":
    """Segment PDF reading text into ``section:<label>`` → body payload.

    The body follows the operative johtolause terminator ("... seuraavasti:"), so
    segmentation starts after the FIRST such terminator to avoid latching onto the
    "7 §, 10 § ..." section list INSIDE the johtolause. Each "N §" header opens a
    segment that runs to the next header (or end). Only flat section bodies are
    recovered here (the solid prose case); chapter/nested targets are left absent
    and thus type-deferred. First-wins on a duplicate label.
    """
    from lawvm.finland.helpers import _normalize_source_section_num
    from lawvm.ingest.page_elements import dehyphenate

    text = dehyphenate(reading_text or "")
    term = _SEURAAVASTI_RE.search(text)
    body = text[term.end():] if term is not None else text

    headers = list(_PDF_BODY_SECTION_RE.finditer(body))
    out: dict[str, str] = {}
    for i, hm in enumerate(headers):
        label = _normalize_source_section_num(hm.group(0))
        if not label:
            continue
        start = hm.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        ref = f"section:{label}"
        if ref not in out:
            out[ref] = body[start:end].strip()
    return out


def _strip_section_header(text: str) -> str:
    """Drop a leading "N §" address header so the payload compares over prose."""
    return _LEADING_SECTION_HEADER_RE.sub("", text or "", count=1).strip()


def _trim(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _PAYLOAD_CANON_TRIM else flat[:_PAYLOAD_CANON_TRIM] + "…"


@dataclass(frozen=True, slots=True)
class PayloadDiffResult:
    """Outcome of the payload stage over one witness pair's matched ops.

    ``divergences`` are the genuine ``payload_mismatch`` records (residual survived
    every inert fold). ``compared`` counts matched ops whose body was present on
    BOTH witnesses and thus actually payload-compared; ``deferred`` counts matched
    ops whose body was absent on ≥1 witness (type-deferred, never forced); ``skipped``
    counts REPEAL tombstones (no payload by construction).
    """

    divergences: "tuple[OpDivergence, ...]"
    compared: int
    deferred: int
    skipped: int


def diff_op_payloads(
    xml_body: "dict[str, str]",
    pdf_body: "dict[str, str]",
    matched_ops: "tuple[FlatOp, ...]",
) -> PayloadDiffResult:
    """Compare the replacement BODY TEXT of each matched op across witnesses.

    For every matched op: REPEAL is skipped (a tombstone has no payload); a target
    whose body is absent on either witness is TYPE-DEFERRED (not payload-comparable
    at this witness pair — thin XML frame / scanned-thin PDF); otherwise the two
    body texts are compared with :func:`text_equivalence` and a surviving residual
    becomes a ``payload_mismatch`` carrying the fired folds + trimmed canon forms.
    """
    out: list[OpDivergence] = []
    compared = 0
    deferred = 0
    skipped = 0
    for op in matched_ops:
        if op.action == "repeal":
            skipped += 1
            continue
        ref = op.target_ref
        xml_text = xml_body.get(ref)
        pdf_text = pdf_body.get(ref)
        if xml_text is None or pdf_text is None:
            deferred += 1
            continue
        compared += 1
        eq = text_equivalence(_strip_section_header(xml_text), _strip_section_header(pdf_text))
        if eq.residual:
            folds = ",".join(f.value for f in eq.folds) or "none"
            out.append(
                OpDivergence(
                    kind="payload_mismatch",
                    target_ref=ref,
                    xml_op=op.render,
                    pdf_op=op.render,
                    detail=(
                        f"replacement body differs beyond inert encoding "
                        f"(folds fired: {folds}); xml={_trim(eq.left_canon)!r} "
                        f"pdf={_trim(eq.right_canon)!r}"
                    ),
                )
            )
    return PayloadDiffResult(tuple(out), compared, deferred, skipped)


# --------------------------------------------------------------------------- #
# Locator handling                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StatuteLocator:
    """A resolved Finnish statute id + language, with its farchive locators."""

    sid: str  # "1994/800"
    lang: str  # "fin"

    @property
    def xml_locator(self) -> str:
        return f"finlex://sd/{self.sid}/{self.lang}/main.xml"

    def media_glob(self) -> str:
        return f"finlex://sd/{self.sid}/{self.lang}/media/%.pdf"


_SID_RE = re.compile(r"^(\d{4})/([^/]+)$")
_XML_LOC_RE = re.compile(r"^finlex://sd/(\d{4}/[^/]+)/([a-z]{3})/main\.xml$")
_MEDIA_LOC_RE = re.compile(r"^finlex://sd/(\d{4}/[^/]+)/([a-z]{3})/media/[^/]+\.pdf$")


def parse_statute_locator(spec: str, *, lang: str = "fin") -> StatuteLocator:
    """Resolve ``spec`` (a bare ``YEAR/NUM`` sid or a full finlex locator)."""
    m = _SID_RE.match(spec)
    if m:
        return StatuteLocator(sid=spec, lang=lang)
    m = _XML_LOC_RE.match(spec)
    if m:
        return StatuteLocator(sid=m.group(1), lang=m.group(2))
    m = _MEDIA_LOC_RE.match(spec)
    if m:
        return StatuteLocator(sid=m.group(1), lang=m.group(2))
    raise AmendmentIrCompareError(
        f"fi-amendment-ir-compare: unrecognised statute locator {spec!r} "
        "(want YEAR/NUM, finlex://sd/YEAR/NUM/LANG/main.xml, or a media/*.pdf locator)"
    )


def _read_farchive(farchive: str, locator: str) -> Optional[bytes]:
    from farchive import Farchive

    fa = Farchive(farchive)
    try:
        return fa.get(locator)
    finally:
        fa.close()


def resolve_media_locator(loc: StatuteLocator, farchive: str) -> str:
    """Return the (first) media PDF locator for this statute/language, or raise."""
    from farchive import Farchive

    fa = Farchive(farchive)
    try:
        hits = sorted(fa.locators(loc.media_glob()))
    finally:
        fa.close()
    if not hits:
        raise OperativeClauseNotFound(
            f"fi-amendment-ir-compare: no media PDF for {loc.sid}/{loc.lang} "
            f"(glob {loc.media_glob()})"
        )
    return hits[0]


# --------------------------------------------------------------------------- #
# XML → ops (trusted reference)                                               #
# --------------------------------------------------------------------------- #


def amendment_ops_from_clause_text(text: str, *, statute_id: str) -> ClauseAST:
    """Parse an operative johtolause TEXT to a ``ClauseAST`` via ``parse_clause``.

    This is the single lowering waist both witnesses share: the diff downstream
    is over the ops this produces, so any divergence is attributable to the TEXT
    each side fed in, not to two different parsers.
    """
    from lawvm.finland.johtolause.api import parse_clause

    if not text.strip():
        raise OperativeClauseNotFound(
            f"fi-amendment-ir-compare: empty operative clause for {statute_id}"
        )
    try:
        result = parse_clause(text, statute_id=statute_id)
    except Exception as exc:  # parse_clause is the shared authority; surface loudly
        raise ClauseParseFailed(
            f"fi-amendment-ir-compare: parse_clause crashed for {statute_id}: {exc}"
        ) from exc
    if result.parse_error is not None:
        raise ClauseParseFailed(
            f"fi-amendment-ir-compare: parse_clause reported error for {statute_id}: "
            f"{result.parse_error}"
        )
    return result.clause_ast


def amendment_ops_from_xml(
    statute_locator: Union[str, StatuteLocator],
    farchive: str = _FINLEX_FARCHIVE,
    *,
    lang: str = "fin",
) -> ClauseAST:
    """Trusted-reference amendment ops from a statute's authoritative main.xml.

    Extracts the enacting johtolause (``metadata.get_johtolause``) and lowers it
    through the shared ``parse_clause``.  Raises :class:`XmlIncompleteError` when
    the body is a thin table-frame (``xml_incomplete``), and
    :class:`OperativeClauseNotFound` when no enacting clause is present.
    """
    loc = (
        statute_locator
        if isinstance(statute_locator, StatuteLocator)
        else parse_statute_locator(statute_locator, lang=lang)
    )
    data = _read_farchive(farchive, loc.xml_locator)
    if not data:
        raise AmendmentIrCompareError(
            f"fi-amendment-ir-compare: main.xml not found in {farchive}: {loc.xml_locator}"
        )
    return _xml_ast_from_bytes(data, loc.sid)


def _xml_ast_from_bytes(data: bytes, sid: str) -> ClauseAST:
    """Lower a main.xml's enacting johtolause to a ``ClauseAST``.

    Split out so ``compare_statute`` reads the main.xml bytes ONCE and derives
    both the op AST and the body payloads from them. Raises the same typed
    frame-only / no-clause failures as its caller.
    """
    from lawvm.finland.metadata import get_johtolause
    from lawvm.tools.fi_parse_compare import xml_body_text

    johto = get_johtolause(data)
    if not johto.strip():
        # No enacting clause AND a thin body ⇒ frame-only table amendment (benign,
        # PDF-only); a thin body is what distinguishes it from a parse gap.
        body = xml_body_text(data)
        if len(body) < _XML_BODY_MIN_CHARS:
            raise XmlIncompleteError(
                f"fi-amendment-ir-compare: {sid} main.xml is a thin table-frame "
                f"(body {len(body)} chars < {_XML_BODY_MIN_CHARS}, no enacting johtolause) "
                "— operative content is PDF-only, comparison refused (xml_frame_only)"
            )
        raise OperativeClauseNotFound(
            f"fi-amendment-ir-compare: {sid} main.xml has no enacting johtolause"
        )
    ast = amendment_ops_from_clause_text(johto, statute_id=sid)
    # A johtolause that lowers to ZERO structural ops over a thin body is a
    # frame-only table amendment: the XML→ops reference is empty, so a diff would
    # be meaningless. Flag it benign rather than forcing an all-missing comparison.
    if not flatten_clause_ast(ast):
        body = xml_body_text(data)
        if len(body) < _XML_BODY_MIN_CHARS:
            raise XmlIncompleteError(
                f"fi-amendment-ir-compare: {sid} main.xml johtolause lowers to 0 "
                f"structural ops over a thin body ({len(body)} chars < {_XML_BODY_MIN_CHARS}) "
                "— operative content is PDF-only, comparison refused (xml_frame_only)"
            )
    return ast


# --------------------------------------------------------------------------- #
# PDF → ops (path under test)                                                 #
# --------------------------------------------------------------------------- #


def extract_operative_johtolause(text: str) -> str:
    """Locate the operative enacting clause inside a PDF's reading text.

    The johtolause runs from the first operative verb (passive present OR the
    historical past-participle ministry form) to the terminal "... seuraavasti[:]".
    Mirrors what ``metadata.get_johtolause`` targets structurally in the XML, so
    both witnesses feed ``parse_clause`` the same clause.  In a correctly-ordered
    vision reading of a full gazette page the johtolause is at the top, so the
    FIRST operative verb anchors it.  Raises :class:`OperativeClauseNotFound` when
    no ``... seuraavasti`` clause is present (e.g. an annex-only media PDF).
    """
    from lawvm.ingest.page_elements import dehyphenate

    flat = re.sub(r"[ \t\r\n­]+", " ", dehyphenate(text)).strip()
    verb = _OPERATIVE_VERB_RE.search(flat)
    if verb is None:
        raise OperativeClauseNotFound(
            "fi-amendment-ir-compare: no operative verb in PDF reading text (pdf_incomplete)"
        )
    start = verb.start()
    term = _SEURAAVASTI_RE.search(flat, start)
    if term is None:
        raise OperativeClauseNotFound(
            "fi-amendment-ir-compare: no 'seuraavasti' terminator after the operative "
            "verb in PDF reading text (pdf_incomplete)"
        )
    end = term.end()
    if flat[end:end + 1] == ":":
        end += 1
    return flat[start:end]


def _manifestation(pdf_bytes: bytes, locator: str) -> SourceManifestation:
    return SourceManifestation(
        artifact_digest=hashlib.sha256(pdf_bytes).hexdigest(),
        source_bytes=pdf_bytes,
        locator=locator,
        source_role="gazette",
        fetched_at=datetime.now(tz=timezone.utc),
        media_type="application/pdf",
    )


def pdf_reading_text(
    pdf_locator: str,
    farchive: str = _FINLEX_FARCHIVE,
    *,
    lane: str = "defacsimile",
    max_pages: int = 20,
) -> str:
    """Reconstruct a media PDF's reading text through the cached parse lane.

    ``lane="defacsimile"`` uses the Level-2 vision converge lane (recovers scanned
    gazette text); ``lane="struct_span"`` uses the vision-free born-digital geom
    lane.  Both are cached in the FI ParsedIrStore, so re-runs are cheap.
    """
    from lawvm.tools.fi_parse_compare import (
        _defacsimile_reconstructed_text,
        _lane_reconstructed_text,
    )

    data = _read_farchive(farchive, pdf_locator)
    if not data:
        raise AmendmentIrCompareError(
            f"fi-amendment-ir-compare: media PDF not found in {farchive}: {pdf_locator}"
        )
    man = _manifestation(data, pdf_locator)
    if lane == "defacsimile":
        return _defacsimile_reconstructed_text(man, max_pages)
    if lane == "struct_span":
        return _lane_reconstructed_text(man, max_pages)
    raise AmendmentIrCompareError(
        f"fi-amendment-ir-compare: unknown lane {lane!r} (want defacsimile|struct_span)"
    )


def amendment_ops_from_pdf(
    pdf_locator: Union[str, StatuteLocator],
    farchive: str = _FINLEX_FARCHIVE,
    *,
    lang: str = "fin",
    lane: str = "defacsimile",
    max_pages: int = 20,
    text_fn: Optional[Callable[[], str]] = None,
) -> ClauseAST:
    """Under-test amendment ops from a gazette PDF, via the SAME clause parser.

    Path: media PDF → vision reading text → :func:`extract_operative_johtolause`
    → ``parse_clause`` → ``ClauseAST`` — identical lowering to the XML side, so
    the diff isolates PDF-text faithfulness.

    ``text_fn`` injects the reading text directly (used by the hermetic test to
    supply a scripted fake for the PDF-text side, bypassing the vision backend and
    the farchive).  When given, ``pdf_locator`` is used only to derive the
    ``statute_id`` for parser resolution.
    """
    loc, reading_text = _resolve_pdf_reading_text(
        pdf_locator, farchive, lang=lang, lane=lane, max_pages=max_pages, text_fn=text_fn
    )
    return _pdf_ast_from_reading_text(reading_text, loc.sid)


def _resolve_pdf_reading_text(
    pdf_locator: Union[str, StatuteLocator],
    farchive: str,
    *,
    lang: str,
    lane: str,
    max_pages: int,
    text_fn: Optional[Callable[[], str]],
) -> "tuple[StatuteLocator, str]":
    """Resolve the (locator, reading-text) pair for the PDF witness ONCE.

    Split out so ``compare_statute`` reads the vision text a single time and
    derives BOTH the op AST and the body payloads from it (no double vision read;
    ``pdf_reading_text`` is cache-backed but the split keeps the read explicit).
    """
    if isinstance(pdf_locator, StatuteLocator):
        loc = pdf_locator
        media: Optional[str] = None
    else:
        loc = parse_statute_locator(pdf_locator, lang=lang)
        media = pdf_locator if _MEDIA_LOC_RE.match(pdf_locator) else None

    if text_fn is not None:
        return loc, text_fn()
    media_locator = media or resolve_media_locator(loc, farchive)
    return loc, pdf_reading_text(media_locator, farchive, lane=lane, max_pages=max_pages)


def _pdf_ast_from_reading_text(reading_text: str, sid: str) -> ClauseAST:
    """Lower a PDF reading text's operative johtolause to a ``ClauseAST``."""
    johto = extract_operative_johtolause(reading_text)
    ast = amendment_ops_from_clause_text(johto, statute_id=sid)
    # A real enacting johtolause always names >= 1 structural op. Zero structural
    # ops means the extractor latched onto annex/body prose that merely CONTAINS an
    # operative verb + "seuraavasti" (e.g. an annex "lisätään yksi havainto ...
    # seuraavasti:") — the media PDF is annex-only. Flag pdf_annex_only rather than
    # emitting a hollow clause that would diff as all-ops-missing.
    if not flatten_clause_ast(ast):
        raise OperativeClauseNotFound(
            f"fi-amendment-ir-compare: {sid} PDF reading text yielded a clause with "
            "0 structural ops (annex/body prose, not an enacting johtolause) — pdf_annex_only"
        )
    return ast


# --------------------------------------------------------------------------- #
# Top-level comparison + report                                              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CompareResult:
    """The full outcome of one statute's XML↔PDF amendment-IR comparison."""

    sid: str
    lang: str
    compare_status: str  # "compared" | "xml_frame_only" | "pdf_annex_only" | "error"
    divergences: tuple[OpDivergence, ...]
    xml_op_count: int
    pdf_op_count: int
    detail: str = ""
    #: Payload-stage census over the MATCHED ops (only meaningful when
    #: ``compare_status == "compared"``): how many had their replacement body text
    #: actually compared on both witnesses, how many were type-deferred (body absent
    #: on ≥1 witness), and how many were REPEAL tombstones (no payload).
    payload_compared: int = 0
    payload_deferred: int = 0
    payload_skipped: int = 0

    @property
    def counts(self) -> "dict[str, int]":
        c = {k: 0 for k in DIVERGENCE_KINDS}
        for d in self.divergences:
            c[d.kind] = c.get(d.kind, 0) + 1
        return c

    @property
    def typed_divergence_count(self) -> int:
        """Number of NON-matched (typed) divergences — the exactness defect count."""
        return sum(1 for d in self.divergences if d.kind != _BENIGN_MATCH)

    @property
    def exact_equivalent(self) -> bool:
        """True iff the PDF IR is EXACTLY the XML IR (zero typed divergences).

        Only meaningful when ``compare_status == "compared"``.
        """
        return self.compare_status == "compared" and self.typed_divergence_count == 0


def compare_statute(
    statute_locator: Union[str, StatuteLocator],
    farchive: str = _FINLEX_FARCHIVE,
    *,
    lang: str = "fin",
    lane: str = "defacsimile",
    max_pages: int = 20,
    pdf_text_fn: Optional[Callable[[], str]] = None,
    xml_locator: Optional[str] = None,
    pdf_locator: Optional[str] = None,
) -> CompareResult:
    """Run both IR paths for one statute and diff them at the OPERATION level.

    Typed strata are returned as a status, never raised past this boundary:
    ``xml_incomplete`` (thin table-frame main.xml) and ``pdf_incomplete`` (annex
    media PDF with no operative johtolause) are FLAGGED, not forced.
    """
    loc = (
        statute_locator
        if isinstance(statute_locator, StatuteLocator)
        else parse_statute_locator(statute_locator, lang=lang)
    )
    xml_spec: Union[str, StatuteLocator] = xml_locator if xml_locator else loc
    pdf_spec: Union[str, StatuteLocator] = pdf_locator if pdf_locator else loc

    # --- XML witness: read the main.xml bytes ONCE; derive ops + body payloads. ---
    try:
        xml_loc = (
            xml_spec
            if isinstance(xml_spec, StatuteLocator)
            else parse_statute_locator(xml_spec, lang=lang)
        )
        xml_data = _read_farchive(farchive, xml_loc.xml_locator)
        if not xml_data:
            raise AmendmentIrCompareError(
                f"fi-amendment-ir-compare: main.xml not found in {farchive}: {xml_loc.xml_locator}"
            )
        xml_ast = _xml_ast_from_bytes(xml_data, loc.sid)
    except XmlIncompleteError as exc:
        return CompareResult(loc.sid, loc.lang, "xml_frame_only", (), 0, 0, str(exc))
    except AmendmentIrCompareError as exc:
        return CompareResult(loc.sid, loc.lang, "error", (), 0, 0, str(exc))

    xml_flat = flatten_clause_ast(xml_ast)

    # --- PDF witness: read the reading text ONCE; derive ops + body payloads. ---
    try:
        _pdf_loc, reading_text = _resolve_pdf_reading_text(
            pdf_spec, farchive, lang=lang, lane=lane, max_pages=max_pages, text_fn=pdf_text_fn
        )
        pdf_ast = _pdf_ast_from_reading_text(reading_text, loc.sid)
    except OperativeClauseNotFound as exc:
        return CompareResult(
            loc.sid, loc.lang, "pdf_annex_only", (), len(xml_flat), 0, str(exc)
        )
    except AmendmentIrCompareError as exc:
        return CompareResult(
            loc.sid, loc.lang, "error", (), len(xml_flat), 0, str(exc)
        )

    pdf_flat = flatten_clause_ast(pdf_ast)
    divergences = diff_amendment_ops(xml_flat, pdf_flat)

    # --- Payload stage: compare the replacement BODY TEXT of the matched ops. ---
    matched_refs = {d.target_ref for d in divergences if d.kind == _BENIGN_MATCH}
    matched_ops = tuple(op for op in xml_flat if op.target_ref in matched_refs)
    xml_body = _xml_body_payloads(xml_data)
    pdf_body = _pdf_body_payloads(reading_text)
    payload = diff_op_payloads(xml_body, pdf_body, matched_ops)

    return CompareResult(
        loc.sid,
        loc.lang,
        "compared",
        divergences + payload.divergences,
        len(xml_flat),
        len(pdf_flat),
        payload_compared=payload.compared,
        payload_deferred=payload.deferred,
        payload_skipped=payload.skipped,
    )


def result_to_json(result: CompareResult) -> dict:
    return {
        "sid": result.sid,
        "lang": result.lang,
        "compare_status": result.compare_status,
        "detail": result.detail,
        "xml_op_count": result.xml_op_count,
        "pdf_op_count": result.pdf_op_count,
        "counts": result.counts,
        "typed_divergence_count": result.typed_divergence_count,
        "exact_equivalent": result.exact_equivalent,
        "payload_compared": result.payload_compared,
        "payload_deferred": result.payload_deferred,
        "payload_skipped": result.payload_skipped,
        "divergences": [
            {
                "kind": d.kind,
                "target_ref": d.target_ref,
                "xml_op": d.xml_op,
                "pdf_op": d.pdf_op,
                "detail": d.detail,
            }
            for d in result.divergences
        ],
    }


_KIND_GLYPH = {
    "matched": "=",
    "op_missing_in_pdf": "-",
    "op_extra_in_pdf": "+",
    "kind_mismatch": "~",
    "payload_mismatch": "≠",
}


def _print_result(result: CompareResult) -> None:
    print(f"fi-amendment-ir-compare  {result.sid}/{result.lang}")
    print("=" * 78)
    if result.compare_status != "compared":
        benign = result.compare_status in {"xml_frame_only", "pdf_annex_only"}
        print(f"STATUS: {result.compare_status}  ({'benign terminal' if benign else 'error'})")
        print(f"  {result.detail}")
        if result.xml_op_count:
            print(f"  (XML→ops produced {result.xml_op_count} ops)")
        return
    c = result.counts
    print(
        f"XML ops={result.xml_op_count}  PDF ops={result.pdf_op_count}   "
        f"matched={c['matched']}  op_missing_in_pdf={c['op_missing_in_pdf']}  "
        f"op_extra_in_pdf={c['op_extra_in_pdf']}  kind_mismatch={c['kind_mismatch']}  "
        f"payload_mismatch={c['payload_mismatch']}"
    )
    print(
        f"  payload stage: compared={result.payload_compared}  "
        f"deferred={result.payload_deferred}  repeal_skipped={result.payload_skipped}"
    )
    print("-" * 78)
    for d in result.divergences:
        g = _KIND_GLYPH.get(d.kind, "?")
        if d.kind == "matched":
            print(f"  {g} {d.target_ref:<40} {d.xml_op}")
        elif d.kind == "op_missing_in_pdf":
            print(f"  {g} {d.target_ref:<40} XML:{d.xml_op}  (dropped by PDF)")
        elif d.kind == "op_extra_in_pdf":
            print(f"  {g} {d.target_ref:<40} PDF:{d.pdf_op}  (not in XML)")
        elif d.kind == "payload_mismatch":
            print(f"  {g} {d.target_ref:<40} {d.detail}")
        else:
            print(f"  {g} {d.target_ref:<40} xml={d.xml_op}  pdf={d.pdf_op}")
    print("-" * 78)
    verdict = "PASS (exact op-set equivalence)" if result.exact_equivalent else (
        f"FAIL ({result.typed_divergence_count} typed divergence(s) to escalate)"
    )
    print(
        f"EXACT op-set equivalence: {verdict}   "
        f"[{c['matched']}/{len(result.divergences)} ops matched]"
    )


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-amendment-ir-compare``."""
    farchive = args.farchive or _FINLEX_FARCHIVE
    lang = args.lang or "fin"

    if args.statute:
        loc = parse_statute_locator(args.statute, lang=lang)
        result = compare_statute(
            loc, farchive, lang=lang, lane=args.lane, max_pages=args.max_pages
        )
    else:
        if not (args.xml and args.pdf):
            raise SystemExit(
                "fi-amendment-ir-compare: pass a STATUTE (YEAR/NUM) or both --xml and --pdf"
            )
        xloc = parse_statute_locator(args.xml, lang=lang)
        result = compare_statute(
            xloc,
            farchive,
            lang=lang,
            lane=args.lane,
            max_pages=args.max_pages,
            xml_locator=args.xml,
            pdf_locator=args.pdf,
        )

    if args.json:
        print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    else:
        _print_result(result)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result_to_json(result), fh, ensure_ascii=False, indent=2)
