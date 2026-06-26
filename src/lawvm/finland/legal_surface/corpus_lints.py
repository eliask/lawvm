"""Cross-statute STRUCTURAL TYPE-MISMATCH lint over the corpus Legal Surface Graph.

A static analyzer for law. It walks the cross-statute corpus graph
(``corpus_graph.build_corpus_surface_graph``) and, for every
``legal_address_entity`` target that carries incoming ``refers_to`` citations,
loads the TARGET statute's body, parses its provision structure, and checks
whether each citing address path actually resolves to an existing provision of
the structural TYPE the path claims.

What it flags (both are SURFACE FACTS, never legal conclusions — §D6/§D7):

  * ``reference.target_provision_absent`` — the cited path names a leaf
    (momentti / kohta) that does not exist in the target's structure (e.g.
    "X lain 5 §:n 4 momentti" but 5 § has only 2 momenttia).
  * ``reference.structural_type_mismatch`` — the cited path's structural TYPE
    disagrees with what is actually at that position (e.g. the citation names a
    *momentti* at depth 2 but 5 § is a flat section with no ``<subsection>``
    children at all, so depth 2 there is a *kohta*, not a *momentti*).

This is DISTINCT from ``broken_detection`` (repeal / renumber over TIME): a
type-mismatch is a structural disagreement at a SINGLE surface time.

Fail-loud, TAG-DON'T-GUESS discipline (the authority firewall):

  * A mismatch is emitted ONLY when it is provable from BOTH sides — the citing
    path parsed AND the target structure parsed AND they disagree. Any
    uncertainty (target body unavailable, body not parseable, an address tail
    this lint does not model deterministically) yields NO finding for that
    citation. Uncertainty is never a fabricated mismatch.
  * The address-tail → structural-type mapping follows the same ordinal
    semantics the Finland replay dispatcher uses
    (``apply_subsection_dispatch.classify_subsection_dispatch_failure``):
    a momentti is the n-th ``<subsection>`` child of the section (1-based), and
    a kohta is the n-th ``<paragraph>`` child of that subsection (1-based).

Structure parsing: a deliberately MINIMAL, direct lxml walk over the
consolidated AKN body (``<section>`` → ``<subsection>`` → ``<paragraph>``). The
heavier Finland IR builders (``xml_ir`` / ``apply_*``) are coupled to the replay
engine and carry replay authority; a surface-only lint must stay independent of
that authority, so it reads the already-normalized consolidated body directly.
Section labels reuse the shared ``helpers._normalize_source_section_num`` so
``"5 §"`` and ``"5"`` compare equal exactly as replay compares them.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

import lxml.etree as etree

from lawvm.core.legal_surface_graph import LegalSurfaceGraph, SourceSpanRef
from lawvm.core.legal_surface_lints import SurfaceLint
from lawvm.finland.helpers import _normalize_source_section_num
from lawvm.finland.legal_surface.body_source import read_reference_body

LINT_PASS_ID = "fi.corpus.type_mismatch.v0"
JURISDICTION = "fi"

_RULE_ABSENT = "fi.corpus.lints.v0.target_provision_absent"
_RULE_MISMATCH = "fi.corpus.lints.v0.structural_type_mismatch"

KIND_ABSENT = "reference.target_provision_absent"
KIND_MISMATCH = "reference.structural_type_mismatch"

# What the depth of a provision tail (``section[/subsection[/item]]``) CLAIMS the
# cited structural type to be, in Finnish drafting vocabulary.
_DEPTH_KIND = {1: "pykälä", 2: "momentti", 3: "kohta"}

# The overclaims a type-mismatch lint must never be read as making. A structural
# surface disagreement is NOT a legal conclusion about validity or defect.
_FORBIDDEN_OVERCLAIMS = (
    "the citing statute is legally defective",
    "the citation is legally invalid",
    "the target provision was repealed or renumbered",
)


class _StoreLike(Protocol):
    """The archive-store surface this lint reads (same shape corpus_graph uses)."""

    def read_oracle(self, sid: str) -> bytes | None: ...
    def read_source(self, sid: str) -> bytes | None: ...
    def read_amendment(self, sid: str) -> bytes | None: ...


def _localname(el: etree._Element) -> str:
    tag = el.tag
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1]
    return ""


def _num_text(el: etree._Element) -> str | None:
    num_el = el.find("{*}num")
    if num_el is None:
        num_el = el.find("num")
    if num_el is None or not num_el.text:
        return None
    return num_el.text.strip()


def _read_body(store: _StoreLike, sid: str) -> bytes | None:
    """Best available body XML (oracle preferred), mirroring corpus_graph policy.

    Delegates to :func:`read_reference_body`: archive-only reads; oracle absence
    OR a ``contentAbsent`` stub falls back to source/amendment so repealed/expired
    statutes still contribute. Any read error is swallowed to ``None`` so an
    unreadable target produces NO finding (the tag-don't-guess rule).
    """
    return read_reference_body(store, sid)


# ── target structure parsing (minimal, deterministic) ───────────────────────


@dataclass(frozen=True, slots=True)
class _SectionStructure:
    """The deterministic structural shape of one section in the target body.

    ``subsection_count`` is the number of ``<subsection>`` (momentti) children.
    ``paragraphs_per_subsection`` is, per momentti (1-based key), the count of
    ``<paragraph>`` (kohta) children. A flat section (no ``<subsection>``) has
    ``subsection_count == 0``.

    ``unmodeled_kohta_subsections`` are the (1-based) momentti ordinals whose
    kohta enumeration this lint does NOT model ordinally — a momentti carrying a
    ``<list>``/``<blockList>`` where kohdat may live as ``<item>`` rather than as
    direct ``<paragraph>`` children. A depth-3 (kohta) citation into such a
    momentti yields NO finding (tag-don't-guess): the ordinal count is not
    deterministically known, so absence cannot be proven.
    """

    label: str
    subsection_count: int
    paragraphs_per_subsection: dict[int, int]
    unmodeled_kohta_subsections: frozenset[int]


def _parse_target_sections(xml_bytes: bytes) -> dict[str, _SectionStructure] | None:
    """Parse target body into ``{normalized_section_label: _SectionStructure}``.

    Returns ``None`` when the body cannot be parsed deterministically (malformed
    XML, or two sections collide on a normalized label so resolution would be
    ambiguous) — the caller then emits NOTHING for that target (tag-don't-guess).
    """
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return None
    out: dict[str, _SectionStructure] = {}
    for sec in root.iter():
        if _localname(sec) != "section":
            continue
        raw = _num_text(sec)
        if raw is None:
            continue
        label = _normalize_source_section_num(raw)
        if not label:
            continue
        subs = [c for c in sec if _localname(c) == "subsection"]
        paras: dict[int, int] = {}
        unmodeled: set[int] = set()
        for i, sub in enumerate(subs, start=1):
            child_kinds = [_localname(c) for c in sub]
            paras[i] = sum(1 for k in child_kinds if k == "paragraph")
            if any(k in ("list", "blockList") for k in child_kinds):
                unmodeled.add(i)
        struct = _SectionStructure(
            label=label,
            subsection_count=len(subs),
            paragraphs_per_subsection=paras,
            unmodeled_kohta_subsections=frozenset(unmodeled),
        )
        if label in out and out[label] != struct:
            # Ambiguous: two sections normalize to the same label with diverging
            # shape — refuse to resolve rather than pick one (tag-don't-guess).
            return None
        out[label] = struct
    return out


# ── address-tail parsing ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _AddressPath:
    """The cited provision tail, split into structural levels.

    ``section`` is the (un-normalized) section label as it appears in the
    address. ``subsection`` / ``item`` are 1-based ordinals when present and
    integer-shaped; ``None`` otherwise. ``depth`` is the number of present
    levels (1 = pykälä, 2 = momentti, 3 = kohta).
    """

    section: str
    subsection: int | None
    item: int | None
    depth: int


def _parse_address_tail(tail: str) -> _AddressPath | None:
    """Parse an address tail (``"5"``, ``"5/2"``, ``"5/2/3"``) deterministically.

    Returns ``None`` when the tail does not parse to a section-rooted ordinal
    path this lint can resolve (empty, non-integer momentti/kohta ordinal, or
    deeper than kohta) — the caller then emits NOTHING for that citation.
    """
    parts = [p for p in tail.split("/") if p != ""]
    if not parts:
        return None
    if len(parts) > 3:
        return None
    section = parts[0]
    if not section:
        return None
    subsection: int | None = None
    item: int | None = None
    if len(parts) >= 2:
        if not parts[1].isdigit():
            return None
        subsection = int(parts[1])
        if subsection < 1:
            return None
    if len(parts) == 3:
        if not parts[2].isdigit():
            return None
        item = int(parts[2])
        if item < 1:
            return None
    return _AddressPath(
        section=section, subsection=subsection, item=item, depth=len(parts)
    )


# ── the lint check ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Finding:
    kind: str
    rule_id: str
    message: str


def _check_citation(
    path: _AddressPath, sections: dict[str, _SectionStructure]
) -> _Finding | None:
    """Compare a parsed cited path against the parsed target structure.

    Returns a finding only when a mismatch/absence is PROVABLE from both sides.
    When the target section itself is not present this returns ``None`` — a
    missing section is a different (broken-detection / renumber-over-time)
    concern, not a single-time structural-type mismatch, and is left untouched
    so this lint never overlaps with ``broken_detection`` (tag-don't-guess).
    """
    sec_label = _normalize_source_section_num(path.section)
    struct = sections.get(sec_label)
    if struct is None:
        # Section absent: out of scope for a single-time structural type check.
        return None

    cited_kind = _DEPTH_KIND[path.depth]

    if path.depth == 1:
        # Section exists; a bare-§ citation has nothing deeper to disagree about.
        return None

    # depth >= 2: the path claims a momentti (and maybe a kohta below it).
    if struct.subsection_count == 0:
        # The section is FLAT (no <subsection> children) yet the citation names
        # a momentti at depth 2 — the cited structural TYPE disagrees with the
        # actual structure (depth 2 in a flat section is a kohta, not a
        # momentti). A genuine structural-type mismatch.
        return _Finding(
            kind=KIND_MISMATCH,
            rule_id=_RULE_MISMATCH,
            message=(
                f"citation names a {cited_kind} at "
                f"{sec_label} §:n {path.subsection}. {cited_kind} "
                f"but target {sec_label} § is a flat section with no momentit "
                f"(0 subsection children); the cited structural type disagrees "
                f"with the target's actual structure"
            ),
        )

    if path.subsection is not None and path.subsection > struct.subsection_count:
        # The momentti ordinal is out of range: that momentti does not exist.
        return _Finding(
            kind=KIND_ABSENT,
            rule_id=_RULE_ABSENT,
            message=(
                f"citation names {sec_label} §:n {path.subsection}. momentti "
                f"but target {sec_label} § has only {struct.subsection_count} "
                f"momentti(a); the cited momentti does not exist"
            ),
        )

    if path.depth == 2:
        # momentti exists and is in range — nothing to disagree about.
        return None

    # depth == 3: the path claims a kohta inside the (in-range) momentti.
    assert path.subsection is not None and path.item is not None
    if path.subsection in struct.unmodeled_kohta_subsections:
        # The momentti carries a <list>/<blockList>; kohdat may live as <item>
        # there, which this lint does not count ordinally → no finding (the
        # absence cannot be proven; tag-don't-guess).
        return None
    kohta_count = struct.paragraphs_per_subsection.get(path.subsection, 0)
    if kohta_count == 0:
        # The momentti has no <paragraph> (kohta) children: the cited kohta is
        # absent (and depth 3 here would not be a kohta — a structural absence).
        return _Finding(
            kind=KIND_ABSENT,
            rule_id=_RULE_ABSENT,
            message=(
                f"citation names {sec_label} §:n {path.subsection}. momentin "
                f"{path.item}. kohta but that momentti has no kohdat "
                f"(0 paragraph children); the cited kohta does not exist"
            ),
        )
    if path.item > kohta_count:
        return _Finding(
            kind=KIND_ABSENT,
            rule_id=_RULE_ABSENT,
            message=(
                f"citation names {sec_label} §:n {path.subsection}. momentin "
                f"{path.item}. kohta but that momentti has only {kohta_count} "
                f"kohta(a); the cited kohta does not exist"
            ),
        )
    return None


# ── graph walk ────────────────────────────────────────────────────────────────


def _citing_surface_text(graph: LegalSurfaceGraph, citing_node_id: str) -> str:
    """Best self-evidencing surface text for a citing reference_resolution.

    Prefers the paired ``reference_expr`` surface (the citation as drafted);
    falls back to the resolution node's own ``surface_text`` payload. Empty
    string when neither carries surface (the lint still embeds the cited path).
    """
    resolution = graph.nodes.get(citing_node_id)
    if resolution is None:
        return ""
    # resolution -> expr via resolution_of edge
    for edge in graph.edges:
        if edge.edge_kind == "resolution_of" and edge.src == citing_node_id:
            expr = graph.nodes.get(edge.dst)
            if expr is not None:
                surf = expr.payload.get("surface_text")
                if isinstance(surf, str) and surf:
                    return surf
            break
    surf = resolution.payload.get("surface_text")
    return surf if isinstance(surf, str) else ""


def _lint_id(target_entity_id: str, citing_node_id: str, kind: str) -> str:
    seed = f"{LINT_PASS_ID}|{kind}|{target_entity_id}|{citing_node_id}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"{LINT_PASS_ID}::{digest}"


def lint_corpus_type_mismatches(
    corpus_graph: LegalSurfaceGraph, store: _StoreLike
) -> list[SurfaceLint]:
    """Flag cited structural-type / leaf-existence disagreements over the corpus.

    For each ``legal_address_entity`` target with incoming ``refers_to`` edges,
    load and parse the target body, then for each citing reference check whether
    the cited path resolves to an existing provision of the claimed structural
    type. Emits a ``SurfaceLint`` per provable disagreement; uncertainty
    (unreadable / unparseable target, unmodeled address tail) emits nothing.

    Returns lints sorted by ``lint_id`` for determinism.
    """
    # Index refers_to edges by their address-entity destination.
    incoming: dict[str, list[str]] = {}
    for edge in corpus_graph.edges:
        if edge.edge_kind != "refers_to":
            continue
        dst = corpus_graph.nodes.get(edge.dst)
        if dst is None or dst.node_kind != "legal_address_entity":
            continue
        incoming.setdefault(edge.dst, []).append(edge.src)

    # Cache parsed target structures per work_id (None == unreadable/unparseable).
    parsed_cache: dict[str, dict[str, _SectionStructure] | None] = {}

    lints: list[SurfaceLint] = []
    for target_entity_id, citing_node_ids in incoming.items():
        target = corpus_graph.nodes[target_entity_id]
        work_id = target.payload.get("work_id")
        address = target.payload.get("address")
        if not isinstance(work_id, str) or not isinstance(address, str):
            continue

        path = _parse_address_tail(address)
        if path is None:
            # Address tail this lint does not model deterministically → skip.
            continue

        if work_id not in parsed_cache:
            body = _read_body(store, work_id)
            parsed_cache[work_id] = (
                _parse_target_sections(body) if body is not None else None
            )
        sections = parsed_cache[work_id]
        if sections is None:
            # Target body unavailable or unparseable → no finding (no guessing).
            continue

        finding = _check_citation(path, sections)
        if finding is None:
            continue

        for citing_node_id in sorted(set(citing_node_ids)):
            citing = corpus_graph.nodes.get(citing_node_id)
            if citing is None:
                continue
            surface = _citing_surface_text(corpus_graph, citing_node_id)
            source_refs: tuple[SourceSpanRef, ...] = (
                (citing.source_ref,) if citing.source_ref is not None else ()
            )
            citing_work = (
                citing.source_ref.work_id if citing.source_ref is not None else None
            )
            # Self-evidencing message: embeds the offending citing surface text,
            # the citing statute, the cited path, and the target's actual shape.
            message = (
                f"{finding.message}. Citing surface text: {surface!r} "
                f"(in statute {citing_work}); cited target {work_id} §{address}"
            )
            lints.append(
                SurfaceLint(
                    lint_id=_lint_id(target_entity_id, citing_node_id, finding.kind),
                    lint_kind=finding.kind,
                    jurisdiction=JURISDICTION,
                    rule_id=finding.rule_id,
                    severity="warning",
                    subject_node_id=citing_node_id,
                    support_node_ids=(target_entity_id,),
                    source_refs=source_refs,
                    message=message,
                    lint_status="active",
                    forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                )
            )

    lints.sort(key=lambda lint: lint.lint_id)
    return lints


@dataclass(frozen=True)
class CorpusTypeMismatchLintPass:
    """``SurfaceLintPass``-shaped wrapper, for use with ``run_lint_passes``.

    The runner only passes the graph, so the store is bound at construction.
    Declares ``surface_only=True`` to satisfy the firewall the runner enforces.
    """

    store: _StoreLike
    lint_pass_id: str = LINT_PASS_ID
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        return tuple(lint_corpus_type_mismatches(graph, self.store))
