"""EU regulation → addressable substrate work + FI→EU reference resolution.

This is the THIRD producer of the distributable substrate, after the FI
engine-replay :mod:`lawvm.substrate.exporter` and the observed-snapshot
:mod:`lawvm.substrate.locus` adapter. It carries the "effective Finnish law in
one set" payoff (design §25.6/§25.8): an acquired EU regulation is ingested as a
language-NEUTRAL, addressable :class:`LegalWork`, and the Finnish corpus's
EU references — today resolved only to an OPAQUE work-level CELEX target — are
upgraded into RESOLVED edges that point at the regulation's own article /
paragraph nodes (the prerequisite for transclusion).

Two halves:

1. **Ingest (STEP 1).** ``parse_consolidated_formex`` reads a CONSLEG
   ``CONS.ACT`` Formex (the consolidated EU manifestation, root ``CONS.ACT`` with
   ``ARTICLE`` / ``PARAG`` carrying numeric ``IDENTIFIER`` like ``006.001``) into
   a flat list of addressable nodes. ``export_eu_regulation_pack`` emits a
   snapshot pack mirroring the LOCUS producer (one ``InitialStateEvent`` of
   genesis ``official_consolidation_checkpoint``, NO replay, struct nodes for
   DIVISION/ARTICLE/PARAG, content leaves, one selection row per addressable
   node). The grafter :mod:`lawvm.eu.grafter` is the FMX4 (non-consolidated, root
   ``ACT``) parser; a CONSLEG manifestation has root ``CONS.ACT`` and is not an
   ``ACT`` descendant, so the grafter raises on it — this module does the direct
   consolidated-Formex structural parse instead.

   **Identity discipline (§25.8).** The Work id is the language-NEUTRAL
   ``celex:32016R0679`` (NOT a Finland-specific work id) — the Finnish text is the
   Finnish EXPRESSION of that one Work. Each article/paragraph addressable node
   carries a stable, CELEX+IDENTIFIER-derived ``eu_entity_node_id`` of the form
   ``entity:celex:32016R0679#006.001`` so it is a stable resolution target. The
   work-entity id and that ``entity:`` prefix come from
   :mod:`lawvm.eu_lex.celex`, the single source of truth shared with the FI side.

2. **Resolve (STEP 2).** ``resolve_fi_eu_edge`` takes the ingested work's node
   index + a FI relation edge whose ``target_set`` carries an article window
   (the FI extractor already serializes ``6 artiklan 1 kohdan a alakohta`` as
   ``celex:32016R0679/6/1/ka`` — article 6, kohta 1, alakohta a). When the
   article window resolves against an ingested node, the edge's opaque CELEX
   target is REWRITTEN to the resolved ``entity:celex:...#<id>`` node id, the
   status is upgraded to ``resolved`` / ``registry_resolved`` on the ``surface``
   plane (matrix-legal). When the work is NOT ingested or the window does not
   resolve, the opaque target + honest status are kept — never a fabricated
   resolution.

Resolution depth. The consolidated Formex addresses articles (``ARTICLE``) and
their numbered paragraphs / kohdat (``PARAG``, ``NNN.MMM``). A point / alakohta
(``a``/``b``/``c``) is NOT a separately ``IDENTIFIER``-addressable node in the
Formex (it lives inside the ``PARAG`` as an ``ALINEA``), so an alakohta-depth FI
window resolves to its enclosing kohta (``PARAG``) node — the deepest node that
genuinely exists — and that depth degradation is recorded honestly (the edge
stays ``resolved`` to a real node, never inventing a phantom alakohta node).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, cast

from lawvm.eu_lex.celex import celex_to_canonical_id, celex_to_entity_id, is_well_formed_celex
from lawvm.substrate.canonical_json import JsonValue, wrap_row
from lawvm.substrate.exporter import (
    CANON_PROFILE,
    IDENTITY_ENCODING,
    STORAGE_CODEC,
    _LayerWriter,
    _coverage_body,
    _git_commit,
    _work_body,
)
from lawvm.substrate.locus import (
    SCHEMA_CONTENT_LEAF,
    SCHEMA_NODE_VERSION,
    _content_leaf_body,
)
from lawvm.substrate.manifest import PackManifest, PackProvenance
from lawvm.substrate.relation_edge import (
    AuthorityPlane,
    EdgeStatus,
    RelationKind,
    TargetSetSemantics,
    VerificationLevel,
    build_relation_edge,
    edge_authority_violation,
)
from lawvm.substrate.roots import (
    leaf_hash,
    map_root,
    seq_root,
    set_root,
)
from lawvm.substrate.selection import (
    ApplicabilityFact,
    DecisionBasis,
    PROFILE_GOVERNING_TEXT,
    ScopePredicate,
    SelectionCandidate,
    SelectionCandidateSet,
    SelectionRow,
    SelectionUniverse,
    TemporalBasis,
    build_selection_index_roots,
    build_state_selection_roots,
    v0_profiles,
)
from lawvm.substrate.source import (
    Availability,
    GenesisKind,
    InitialStateEvent,
    Locator,
    LogicalKind,
    PriorHistoryStatus,
    SourceManifestation,
    SourceRecord,
)

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

PACK_KIND = "lawvm.pack.snapshot.v0"
JURISDICTION = "eu"

# The EU consolidated manifestation is observed at one codification checkpoint —
# no per-node effective dates (this producer does not replay the amendment
# history). A single fixed account/effect date makes every selected node
# constant over ``[INGEST_DATE, +inf)`` (a corpus-account fact, NOT a legislative
# commencement claim). Overridable per call for determinism.
DEFAULT_INGEST_DATE = "2021-03-04"  # GDPR consolidated CONSLEG.DATE for this file

_PROFILE_ID = PROFILE_GOVERNING_TEXT
_BRANCH_ID = "actual"
_RAIL_PERMANENT = "permanent"

# Structural kinds the consolidated Formex carries, mapped to substrate kinds.
_KIND_DIVISION = "division"
_KIND_ARTICLE = "article"
_KIND_PARAG = "paragraph"

# A FI EU-reference target serialized by the FI extractor: ``celex:<CELEX>`` for
# the opaque work-level cite, ``celex:<CELEX>/<article>[/<kohta>][/k<alakohta>]``
# for an article-windowed cite (``ProvisionRef.serialized()`` form).
_FI_CELEX_TARGET = re.compile(r"^celex:(?P<celex>[0-9][0-9A-Za-z]+)(?P<rest>/.*)?$")


# --------------------------------------------------------------------------- #
# STEP 0/1 — consolidated Formex structural parse                              #
# --------------------------------------------------------------------------- #


def _formex_text(el: ET.Element[str]) -> str:
    """Collect inner text of a Formex element, whitespace-normalized."""
    return " ".join("".join(el.itertext()).split())


@dataclass(frozen=True, slots=True)
class EuStructNode:
    """One addressable node parsed from a consolidated Formex.

    ``identifier`` is the Formex ``IDENTIFIER`` (``006`` for ARTICLE,
    ``006.001`` for PARAG) — the stable address skeleton. ``address_path`` is the
    substrate canonical path (``article:006`` / ``article:006/paragraph:006.001``)
    used as the ``struct_node_id`` address input; ``entity_node_id`` is the
    language-neutral CELEX+IDENTIFIER target id (``entity:celex:32016R0679#006``).
    """

    structural_kind: str
    identifier: str
    label: str
    title: str
    text: str
    address_path: str
    entity_node_id: str
    article_number: str
    parag_number: str | None


def _entity_node_id(celex: str, identifier: str) -> str:
    """``entity:celex:<CELEX>#<IDENTIFIER>`` — the addressable, stable target id.

    The work-entity prefix (``entity:celex:<CELEX>``) comes from the shared
    :func:`lawvm.eu_lex.celex.celex_to_entity_id`, so the EU side and the FI
    frontier node agree by construction; the ``#<IDENTIFIER>`` fragment is the
    article/paragraph address WITHIN the work.
    """
    return f"{celex_to_entity_id(celex)}#{identifier}"


def parse_consolidated_formex(xml_path: str | Path, *, celex: str) -> list[EuStructNode]:
    """Parse a CONSLEG ``CONS.ACT`` Formex into a flat, document-ordered node list.

    Walks ``ENACTING.TERMS`` for ``DIVISION`` (chapter/section containers, which
    carry no ``IDENTIFIER`` in this manifestation — addressed positionally),
    ``ARTICLE`` (``IDENTIFIER`` like ``006``) and their child ``PARAG``
    (``IDENTIFIER`` like ``006.001``). The grafter (:mod:`lawvm.eu.grafter`) is
    the non-consolidated FMX4 (root ``ACT``) parser and RAISES on a ``CONS.ACT``
    root, so this direct parse is the consolidated path.

    Raises ``ValueError`` if the root is not ``CONS.ACT`` (fail-loud — never a
    silent empty parse of an unexpected manifestation).
    """
    if not is_well_formed_celex(celex):
        raise ValueError(f"not a well-formed CELEX id: {celex!r}")
    tree = ET.parse(str(xml_path))
    root = tree.getroot()
    if root.tag != "CONS.ACT":
        raise ValueError(
            f"expected a consolidated Formex root 'CONS.ACT', got {root.tag!r}; "
            "non-consolidated FMX4 ('ACT' root) is handled by lawvm.eu.grafter"
        )

    enacting = root.find(".//ENACTING.TERMS")
    if enacting is None:
        raise ValueError("consolidated Formex has no ENACTING.TERMS")

    nodes: list[EuStructNode] = []
    division_counter = 0

    def _emit_article(article_el: ET.Element[str]) -> None:
        ident = article_el.attrib.get("IDENTIFIER")
        if not ident:
            return
        ti = article_el.find("TI.ART")
        title = _formex_text(ti) if ti is not None else ""
        addr = f"article:{ident}"
        nodes.append(
            EuStructNode(
                structural_kind=_KIND_ARTICLE,
                identifier=ident,
                label=ident.lstrip("0") or "0",
                title=title,
                # The article-level text is its title + any direct ALINEA prose
                # not under a PARAG; the substantive text lives in the PARAG
                # children, so the article node keeps only its title.
                text=title,
                address_path=addr,
                entity_node_id=_entity_node_id(celex, ident),
                article_number=ident,
                parag_number=None,
            )
        )
        for parag in article_el.findall("PARAG"):
            pident = parag.attrib.get("IDENTIFIER")
            if not pident:
                continue
            paddr = f"{addr}/paragraph:{pident}"
            nodes.append(
                EuStructNode(
                    structural_kind=_KIND_PARAG,
                    identifier=pident,
                    label=pident.split(".")[-1].lstrip("0") or "0",
                    title="",
                    text=_formex_text(parag),
                    address_path=paddr,
                    entity_node_id=_entity_node_id(celex, pident),
                    article_number=ident,
                    parag_number=pident.split(".")[-1],
                )
            )

    def _walk(el: ET.Element[str], division_path: str) -> None:
        nonlocal division_counter
        for child in el:
            if child.tag == "DIVISION":
                division_counter += 1
                # DIVISIONs carry no IDENTIFIER in this manifestation; address
                # positionally so the container is still a stable, owned node.
                seq = f"{division_counter:03d}"
                ti = child.find("TITLE")
                dtitle = _formex_text(ti) if ti is not None else ""
                daddr = f"{division_path}division:{seq}" if division_path else f"division:{seq}"
                nodes.append(
                    EuStructNode(
                        structural_kind=_KIND_DIVISION,
                        identifier=seq,
                        label=seq.lstrip("0") or "0",
                        title=dtitle,
                        text=dtitle,
                        address_path=daddr,
                        entity_node_id=_entity_node_id(celex, f"div.{seq}"),
                        article_number="",
                        parag_number=None,
                    )
                )
                _walk(child, daddr + "/")
            elif child.tag == "ARTICLE":
                _emit_article(child)

    _walk(enacting, "")
    return nodes


# --------------------------------------------------------------------------- #
# EU address node body (carries the addressable entity node id)                #
# --------------------------------------------------------------------------- #

SCHEMA_ADDRESS_NODE = "lawvm.address_node.v1"
ADDRESS_ID_SCHEMA = "lawvm.address_id.v1"
ADDRESS_PROFILE_ID = "lawvm.address_profile.eu.v0"
_DOMAIN_ADDRESS_NODE = "address_node"


def _eu_address_node_body(
    work_id: str, address_path: str, structural_kind: str, entity_node_id: str
) -> dict[str, JsonValue]:
    """``lawvm.address_node.v1`` carrying the language-neutral ``eu_entity_node_id``.

    Identical schema + content-addressed ``struct_node_id`` to the exporter's
    address node, plus the stable CELEX+IDENTIFIER target id (``entity:celex:
    32016R0679#006``) so the resolved-edge target both EXISTS as a node and is a
    human-addressable handle.
    """
    identity = {
        "identity_schema": ADDRESS_ID_SCHEMA,
        "work_id": work_id,
        "structural_kind": structural_kind,
        "address_path": address_path,
        "special": "",
        "creation_event_id": "",
        "local_discriminator": "",
        "jurisdiction_profile_id": ADDRESS_PROFILE_ID,
    }
    struct_node_id = leaf_hash(_DOMAIN_ADDRESS_NODE, identity)
    return {
        "schema": SCHEMA_ADDRESS_NODE,
        "struct_node_id": struct_node_id,
        "work_id": work_id,
        "structural_kind": structural_kind,
        "address_path": address_path,
        "identity_schema": ADDRESS_ID_SCHEMA,
        "jurisdiction_profile_id": ADDRESS_PROFILE_ID,
        "eu_entity_node_id": entity_node_id,
    }


def _eu_struct_node_id(work_id: str, address_path: str, structural_kind: str) -> str:
    identity = {
        "identity_schema": ADDRESS_ID_SCHEMA,
        "work_id": work_id,
        "structural_kind": structural_kind,
        "address_path": address_path,
        "special": "",
        "creation_event_id": "",
        "local_discriminator": "",
        "jurisdiction_profile_id": ADDRESS_PROFILE_ID,
    }
    return leaf_hash(_DOMAIN_ADDRESS_NODE, identity)


_DOMAIN_NODE_VERSION = "node_version"


def _node_version_body(
    struct_node_id: str,
    content_leaf_hash: str,
    produced_by: str,
    source_locators: list[JsonValue],
    ingest_date: str,
) -> tuple[str, dict[str, JsonValue]]:
    """``lawvm.node_version.v1`` over a single open-ended consolidation interval.

    Mirrors the locus / exporter node_version, but keyed on THIS ingest's
    ``ingest_date`` (the locus variant hardcodes its own SNAPSHOT_DATE, so it is
    re-built here rather than imported). ``source_locators`` ride on the
    node_version (not the shared content leaf), so the leaf stays pure text and
    deduplicates across works.
    """
    identity: dict[str, JsonValue] = {
        "schema": SCHEMA_NODE_VERSION,
        "struct_node_id": struct_node_id,
        "produced_by_transition_id": produced_by,
        "content_leaf_hash": content_leaf_hash,
        "effective_interval": [ingest_date, None],
        "branch_id": _BRANCH_ID,
        "rail": _RAIL_PERMANENT,
    }
    node_version_id = leaf_hash(_DOMAIN_NODE_VERSION, identity)
    body = dict(identity)
    body["node_version_id"] = node_version_id
    body["source_locators"] = list(source_locators)
    return node_version_id, body


# --------------------------------------------------------------------------- #
# The ingested-work handle + node index                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class IngestedEuWork:
    """The addressable index of one ingested EU work (the resolution registry).

    ``work_id`` is the language-neutral ``celex:<CELEX>``. ``article_node`` maps
    an article number (``"6"``, ``"35"``) to its ``entity_node_id``;
    ``parag_node`` maps ``(article, kohta)`` to the PARAG ``entity_node_id``. The
    resolver consults this index to rewrite an opaque FI→EU CELEX target into a
    resolved node id, and to confirm the node EXISTS before claiming resolution.
    """

    celex: str
    work_id: str
    title: str
    nodes: tuple[EuStructNode, ...]
    article_node: dict[str, str]
    parag_node: dict[tuple[str, str], str]
    entity_ids: frozenset[str]

    @classmethod
    def from_nodes(cls, *, celex: str, title: str, nodes: Sequence[EuStructNode]) -> "IngestedEuWork":
        article_node: dict[str, str] = {}
        parag_node: dict[tuple[str, str], str] = {}
        entity_ids: set[str] = set()
        for n in nodes:
            entity_ids.add(n.entity_node_id)
            if n.structural_kind == _KIND_ARTICLE:
                article_node[n.article_number.lstrip("0") or "0"] = n.entity_node_id
            elif n.structural_kind == _KIND_PARAG and n.parag_number is not None:
                key = (n.article_number.lstrip("0") or "0", n.parag_number.lstrip("0") or "0")
                parag_node[key] = n.entity_node_id
        return cls(
            celex=celex,
            work_id=celex_to_canonical_id(celex),
            title=title,
            nodes=tuple(nodes),
            article_node=article_node,
            parag_node=parag_node,
            entity_ids=frozenset(entity_ids),
        )


# --------------------------------------------------------------------------- #
# STEP 2 — FI→EU reference resolution against the ingested work                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TargetResolution:
    """The outcome of resolving ONE opaque FI→EU target string against a work."""

    opaque: str
    resolved: str | None
    celex: str | None
    article: str | None
    kohta: str | None
    alakohta: str | None
    # ``article`` | ``paragraph`` | None — the depth actually resolved (a kohta
    # window resolves to its PARAG node; an alakohta window degrades to the kohta
    # node, the deepest node that exists). None = not resolved.
    resolved_depth: str | None
    reason: str  # ``resolved`` | ``not_ingested`` | ``article_window_absent`` | ``node_absent``

    @property
    def is_resolved(self) -> bool:
        return self.resolved is not None


def parse_fi_eu_target(target: str) -> tuple[str, str | None, str | None, str | None] | None:
    """Parse a FI ``celex:<CELEX>[/<art>[/<kohta>][/k<alakohta>]]`` target.

    Returns ``(celex, article, kohta, alakohta)`` (later parts ``None`` when the
    window is shallower), or ``None`` when the string is not a ``celex:`` target.
    The serialized form (``ProvisionRef.serialized()``) is
    ``celex:<CELEX>/<article>[/<momentti/kohta>][/k<alakohta>]``; for an EU
    article the FIRST path segment is the article, the bare integer second
    segment is the kohta (numbered paragraph), and a ``k<label>`` segment is the
    alakohta point.
    """
    m = _FI_CELEX_TARGET.match(target)
    if m is None:
        return None
    celex = m.group("celex")
    rest = m.group("rest") or ""
    segments = [s for s in rest.split("/") if s]
    article: str | None = None
    kohta: str | None = None
    alakohta: str | None = None
    if segments:
        article = segments[0]
        for seg in segments[1:]:
            if seg.startswith("k") and not seg[1:].isdigit():
                # ``k<letter>`` alakohta (``ka`` → alakohta a). A ``k<number>``
                # form (``k3``) is a kohta-with-no-momentti serialization.
                alakohta = seg[1:]
            elif seg.startswith("k") and seg[1:].isdigit():
                kohta = seg[1:]
            elif seg.startswith("s"):
                # ``s<label>`` alakohta (sub-item) — degrade to kohta depth.
                alakohta = seg[1:]
            elif seg.isdigit() and kohta is None:
                kohta = seg
    return celex, article, kohta, alakohta


def resolve_fi_eu_target(target: str, work: IngestedEuWork | None) -> TargetResolution:
    """Resolve one opaque FI→EU target string against an ingested work.

    Honest, fail-loud depth degradation: an article-only window resolves to the
    ARTICLE node; an article+kohta window to the PARAG node; an alakohta window
    degrades to its kohta PARAG node (the deepest node that genuinely exists in
    the Formex — points are not separately addressable). When the work is not
    ingested, the target carries no article window, or the windowed node is
    absent, ``resolved`` is ``None`` and the opaque target is kept.
    """
    parsed = parse_fi_eu_target(target)
    if parsed is None:
        return TargetResolution(target, None, None, None, None, None, None, "not_celex_target")
    celex, article, kohta, alakohta = parsed
    if work is None or celex != work.celex:
        return TargetResolution(target, None, celex, article, kohta, alakohta, None, "not_ingested")
    if article is None:
        return TargetResolution(
            target, None, celex, article, kohta, alakohta, None, "article_window_absent"
        )
    art_key = article.lstrip("0") or "0"
    if kohta is not None:
        kohta_key = kohta.lstrip("0") or "0"
        node = work.parag_node.get((art_key, kohta_key))
        if node is not None:
            return TargetResolution(
                target, node, celex, article, kohta, alakohta, _KIND_PARAG, "resolved"
            )
        # Kohta node absent → degrade to the article node if present.
    node = work.article_node.get(art_key)
    if node is not None:
        return TargetResolution(
            target, node, celex, article, kohta, alakohta, _KIND_ARTICLE, "resolved"
        )
    return TargetResolution(target, None, celex, article, kohta, alakohta, None, "node_absent")


@dataclass(frozen=True, slots=True)
class EdgeResolution:
    """The outcome of upgrading ONE FI relation edge against an ingested work."""

    edge: dict[str, JsonValue]
    rewritten: bool
    per_target: tuple[TargetResolution, ...]


def resolve_fi_eu_edge(
    edge: dict[str, JsonValue],
    work: IngestedEuWork | None,
    *,
    corpus_version: str,
) -> EdgeResolution:
    """Upgrade a FI ``citation`` edge's CELEX target_set against an ingested work.

    When EVERY ``celex:`` target in the edge's ``target_set`` resolves against
    the ingested work, the edge is REBUILT with the resolved
    ``entity:celex:...#<id>`` node ids, ``status=resolved``,
    ``verification_level=registry_resolved`` on the ``surface`` plane — the
    matrix-legal posture for a deterministically resolved citation. When some
    target does not resolve (work not ingested, no article window, or the node is
    absent), the edge is LEFT UNCHANGED (its opaque target + honest status are
    preserved — no fabricated resolution; honest typed residue).

    The rewritten edge is asserted matrix-legal before returning (a guard, not a
    hope), so the producer can never emit an ``INVALID_EDGE_AUTHORITY`` edge.
    """
    target_set = edge.get("target_set")
    if not isinstance(target_set, list):
        return EdgeResolution(edge, False, ())

    per_target: list[TargetResolution] = []
    has_celex = False
    for t in target_set:
        ts = str(t)
        res = resolve_fi_eu_target(ts, work)
        per_target.append(res)
        if res.celex is not None or ts.startswith("celex:"):
            has_celex = True

    # Only rewrite when this edge is a CELEX (EU) citation AND every CELEX target
    # resolved. A mixed edge with one unresolved CELEX target stays opaque — we
    # do not partially fabricate.
    celex_targets = [r for r in per_target if str(r.opaque).startswith("celex:")]
    if not has_celex or not celex_targets or not all(r.is_resolved for r in celex_targets):
        return EdgeResolution(edge, False, tuple(per_target))

    new_targets = sorted(
        (r.resolved if r.is_resolved and r.resolved is not None else r.opaque)
        for r in per_target
    )

    rebuilt = build_relation_edge(
        relation_kind=RelationKind.CITATION,
        source_ref=str(edge.get("source_ref", "")),
        target_set=tuple(new_targets),
        target_set_semantics=_target_set_semantics(edge),
        authority_plane=AuthorityPlane.SURFACE,
        verification_level=VerificationLevel.REGISTRY_RESOLVED,
        replay_authorized=False,
        edge_status=EdgeStatus.RESOLVED,
        effective_scope=cast("dict[str, JsonValue]", edge.get("effective_scope", {"branch_id": _BRANCH_ID})),
        corpus_version=corpus_version,
        branch_id=str(edge.get("branch_id", _BRANCH_ID)),
        evidence_refs=tuple(str(e) for e in cast("list[Any]", edge.get("evidence_refs", []))),
    )
    reason = edge_authority_violation(rebuilt)
    assert reason is None, f"eu_ingest produced a matrix-ILLEGAL resolved edge: {reason}"
    return EdgeResolution(rebuilt, True, tuple(per_target))


def _target_set_semantics(edge: dict[str, JsonValue]) -> TargetSetSemantics:
    raw = edge.get("target_set_semantics")
    if isinstance(raw, str):
        try:
            return TargetSetSemantics(raw)
        except ValueError:
            pass
    target_set = edge.get("target_set")
    n = len(target_set) if isinstance(target_set, list) else 0
    return TargetSetSemantics.SINGLE if n <= 1 else TargetSetSemantics.ALL_VALID


# --------------------------------------------------------------------------- #
# Result summary                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class EuIngestResult:
    """Summary of one emitted EU regulation snapshot pack (the CLI prints this)."""

    work_id: str
    celex: str
    out_dir: str
    pack_id: str
    n_nodes: int
    n_articles: int
    n_parags: int
    n_divisions: int
    n_content_leaves: int
    n_selection_rows: int
    n_residuals: int


@dataclass
class FiResolveResult:
    """Summary of FI→EU edge resolution over one FI work's relation edges."""

    n_eu_edges: int
    n_resolved: int
    n_opaque: int
    examples: tuple[tuple[str, str], ...]  # (opaque_target, resolved_node) pairs


# --------------------------------------------------------------------------- #
# STEP 1 — the EU snapshot pack producer                                       #
# --------------------------------------------------------------------------- #

_FILLED_LAYERS: tuple[tuple[str, str, str], ...] = (
    ("base", "base/base.jsonl", "SetRoot"),
    ("state", "state/state.jsonl", "SetRoot"),
    ("trace", "trace/trace.jsonl", "SeqRoot"),
    ("proof", "proof/proof.jsonl", "SetRoot"),
)
_RESERVED_DIRS: tuple[str, ...] = ("surface", "edges", "branch", "projection", "dict", "overlay")

SCHEMA_CERTIFICATE = "lawvm.certificate.v0"


def export_eu_regulation_pack(
    formex_path: str | Path,
    *,
    celex: str,
    out_dir: str | Path,
    title: str | None = None,
    nodes: Sequence[EuStructNode] | None = None,
    ingest_date: str = DEFAULT_INGEST_DATE,
    created_at: str | None = None,
    quiet: bool = False,
) -> tuple[EuIngestResult, IngestedEuWork]:
    """Ingest a consolidated EU regulation Formex → addressable snapshot pack.

    Returns the emitted-pack summary AND the :class:`IngestedEuWork` node index
    (the resolution registry the FI side consults). ``nodes`` may be supplied
    directly (tests / synthetic works); otherwise they are parsed from
    ``formex_path`` via :func:`parse_consolidated_formex`.
    """
    if nodes is None:
        nodes = parse_consolidated_formex(formex_path, celex=celex)
    work_title = title or f"EU regulation {celex}"
    work = IngestedEuWork.from_nodes(celex=celex, title=work_title, nodes=nodes)
    work_id = work.work_id  # celex:<CELEX> — language-neutral (§25.8).

    corpus_version = f"{JURISDICTION}:corpus:{celex}:{ingest_date}"
    out = Path(out_dir)
    if out.exists():
        import shutil

        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    writers: dict[str, _LayerWriter] = {}
    for kind, fname, root_fn in _FILLED_LAYERS:
        writers[kind] = _LayerWriter(out / fname, root_fn)
    for reserved in _RESERVED_DIRS:
        (out / reserved).mkdir(parents=True, exist_ok=True)

    base_w = writers["base"]
    state_w = writers["state"]
    proof_w = writers["proof"]

    # -- source plane: one record + one manifestation ------------------------ #
    source_record = SourceRecord(
        jurisdiction=JURISDICTION,
        keeper="eur-lex",
        logical_kind=LogicalKind.ACT_XML,
        logical_key=work_id,
        work_id_hint=work_id,
    )
    base_w.write(source_record.to_canonical_dict())
    manifestation = SourceManifestation(
        source_record_id=source_record.source_record_id,
        raw_witness_hash=_witness_hash(nodes),
        media_type="application/xml",
        fetched_at=ingest_date,
        locator=Locator(scheme="farchive", value=f"farchive:eulex:{celex}", byte_count=None),
        availability=Availability.DIGEST_ONLY,
    )
    base_w.write(manifestation.to_canonical_dict())
    source_ref = manifestation.locator.value

    # -- work + selection profiles + total scope ----------------------------- #
    base_w.write(_work_body(work_id, work_title, JURISDICTION, corpus_version))

    total_scope = ScopePredicate(dimensions={}, scope_status="total")
    scope_predicate_id = total_scope.scope_predicate_id
    scope_predicate_hashes = [state_w.write(total_scope.to_canonical_dict())]

    selection_profile_hashes: list[str] = []
    for prof in v0_profiles():
        selection_profile_hashes.append(state_w.write(prof.to_canonical_dict()))

    # -- genesis: ONE official_consolidation_checkpoint ---------------------- #
    genesis = InitialStateEvent(
        work_id=work_id,
        genesis_kind=GenesisKind.OFFICIAL_CONSOLIDATION_CHECKPOINT,
        effective_date=ingest_date,
        prior_history_status=PriorHistoryStatus.UNMODELED,
        source_refs=(source_ref,),
        creation_event_id=manifestation.manifestation_id,
    )
    base_w.write(genesis.to_canonical_dict())

    # -- per-node emission --------------------------------------------------- #
    content_leaf_hashes: list[str] = []
    node_version_hashes: list[str] = []
    applicability_fact_hashes: list[str] = []
    candidate_set_hashes: list[str] = []
    selection_row_hashes: list[str] = []
    emitted_content_leaves: set[str] = set()
    address_nodes_seen: dict[str, str] = {}  # address_path -> structural_kind
    expected_selection_keys: dict[str, str] = {}
    all_addresses: set[str] = set()

    n_articles = n_parags = n_divisions = 0

    def _ensure_address_node(node: EuStructNode) -> str:
        if node.address_path not in address_nodes_seen:
            address_nodes_seen[node.address_path] = node.structural_kind
            base_w.write(
                _eu_address_node_body(work_id, node.address_path, node.structural_kind, node.entity_node_id)
            )
        return _eu_struct_node_id(work_id, node.address_path, node.structural_kind)

    def _ensure_content_leaf(text: str) -> str:
        clh, body = _content_leaf_body(text)
        if clh not in emitted_content_leaves:
            base_w.write(body)
            emitted_content_leaves.add(clh)
            content_leaf_hashes.append(clh)
        return clh

    for node in nodes:
        if node.structural_kind == _KIND_ARTICLE:
            n_articles += 1
        elif node.structural_kind == _KIND_PARAG:
            n_parags += 1
        elif node.structural_kind == _KIND_DIVISION:
            n_divisions += 1

        struct_id = _ensure_address_node(node)
        all_addresses.add(node.address_path)
        clh = _ensure_content_leaf(node.text)

        produced_by = f"genesis:{node.address_path}"
        nv_id, nv_body = _node_version_body(struct_id, clh, produced_by, [source_ref], ingest_date)
        node_version_hashes.append(state_w.write(nv_body))

        fact = ApplicabilityFact(
            work_id=work_id,
            address_id=struct_id,
            node_version_id=nv_id,
            content_leaf_hash=clh,
            branch_id=_BRANCH_ID,
            effect_interval=(ingest_date, None),
            enactment_interval=(ingest_date, None),
            account_interval=(corpus_version, None),
            rail=_RAIL_PERMANENT,
            scope_predicate_id=scope_predicate_id,
            precedence_class="same_rail_latest",
            temporal_basis=TemporalBasis(kind="source_checkpoint"),
            produced_by_transition_id=produced_by,
        )
        applicability_fact_hashes.append(state_w.write(fact.to_canonical_dict()))

        cand = SelectionCandidate(
            node_version_id=nv_id,
            rail=_RAIL_PERMANENT,
            effect_interval=(ingest_date, None),
            scope_predicate_id=scope_predicate_id,
            eligible=True,
        )
        cset = SelectionCandidateSet(
            selection_key=f"{struct_id}:{ingest_date}",
            candidates=(cand,),
            complete=True,
        )
        cs_object_hash = state_w.write(cset.to_canonical_dict())
        candidate_set_hashes.append(cs_object_hash)

        selrow = SelectionRow(
            work_id=work_id,
            query_profile_id=_PROFILE_ID,
            branch_id=_BRANCH_ID,
            address_id=struct_id,
            scope_query_id=scope_predicate_id,
            effect_interval=(ingest_date, None),
            account_interval=(corpus_version, None),
            source_policy_id="archival_exact",
            selection_status="selected",
            candidate_set_hash=cs_object_hash,
            selected_node_version_id=nv_id,
            decision_basis=DecisionBasis(
                selection_rule_id=_PROFILE_ID,
                applicability_fact_refs=(fact.fact_id,),
            ),
        )
        selection_key = selrow.selection_key
        row_body = selrow.to_canonical_dict()
        row_body["selection_key"] = selection_key
        row_object_hash = state_w.write(row_body)
        selection_row_hashes.append(row_object_hash)
        expected_selection_keys[selection_key] = row_object_hash

    # -- coverage ------------------------------------------------------------ #
    n_residuals = proof_w.row_count
    proof_w.write(_coverage_body("owned", len(selection_row_hashes), "selected EU addressable nodes"))
    proof_w.write(_coverage_body("residual", n_residuals, "typed ingest residuals"))

    # -- selection universe -------------------------------------------------- #
    universe = SelectionUniverse(
        work_id=work_id,
        query_profile_ids=(_PROFILE_ID,),
        branch_ids=(_BRANCH_ID,),
        expected_selection_keys=expected_selection_keys,
        address_root=set_root("address_universe", [leaf_hash("addr", a) for a in sorted(all_addresses)]),
        effect_boundary_root=set_root("effect_boundary", [leaf_hash("effect", ingest_date)]),
        account_boundary_root=set_root("account_boundary", [leaf_hash("account", corpus_version)]),
        scope_query_root=set_root("scope_query", [scope_predicate_id]),
    )
    selection_universe_hashes = [state_w.write(universe.to_canonical_dict())]

    for w in writers.values():
        w.close()

    # -- roots --------------------------------------------------------------- #
    state_roots = build_state_selection_roots(
        selection_profile_object_hashes=selection_profile_hashes,
        selection_universe_object_hashes=selection_universe_hashes,
        scope_predicate_object_hashes=scope_predicate_hashes,
        applicability_fact_object_hashes=applicability_fact_hashes,
        candidate_set_object_hashes=candidate_set_hashes,
        selection_row_object_hashes=selection_row_hashes,
    )
    content_leaf_root = set_root("content_leaf", content_leaf_hashes)
    node_version_root = set_root("node_version", node_version_hashes)
    projection_root = set_root("projection", [])
    index_roots = build_selection_index_roots(
        content_leaf_root=content_leaf_root,
        node_version_root=node_version_root,
        state_selection_root=state_roots.state_selection_root,
        projection_root=projection_root,
    )
    materialization_root = seq_root("materialization", writers["trace"].hashes)

    certificate_root, cert_body = _build_certificate(
        work_id=work_id,
        materialization_root=materialization_root,
        selection_index_root=index_roots.selection_index_root,
        n_residuals=n_residuals,
    )
    cert_dir = out / "cert"
    cert_dir.mkdir(parents=True, exist_ok=True)
    (cert_dir / "certificate.json").write_text(
        json.dumps(wrap_row(cert_body), ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    source_bundle_root = leaf_hash("source_bundle", {"source_refs": [source_ref]})
    roots = {
        "materialization_root": materialization_root,
        "selection_index_root": index_roots.selection_index_root,
        "certificate_root": certificate_root,
        "source_bundle_root": source_bundle_root,
    }
    layers = _build_layer_descriptors(writers)

    schemas = {
        "work": "lawvm.work.v1",
        "address_node": SCHEMA_ADDRESS_NODE,
        "content_leaf": SCHEMA_CONTENT_LEAF,
        "node_version": SCHEMA_NODE_VERSION,
        "selection_row": "lawvm.selection_row.v1",
        "applicability_fact": "lawvm.applicability_fact.v1",
        "initial_state_event": "lawvm.initial_state_event.v1",
    }
    provenance = PackProvenance(
        lawvm_git_commit=_git_commit(),
        engine_version="lawvm.snapshot.eu_ingest",
        source_policy_id="archival_exact",
        checkable_source_bundle_policy="archival_exact",
        created_at=created_at or _dt.datetime.now(_dt.timezone.utc).isoformat(),
        dirty_tree=False,
    )
    manifest = PackManifest(
        pack_kind=PACK_KIND,
        work_ids=(work_id,),
        corpus_version=corpus_version,
        identity_encoding=IDENTITY_ENCODING,
        storage_codec=STORAGE_CODEC,
        dict_id="",
        profiles=(CANON_PROFILE,),
        selection_profiles=(_PROFILE_ID,),
        schemas=schemas,
        layers=layers,
        roots=roots,
        required_layers_for_browse=("base", "state", "cert"),
        required_layers_for_audit=("base", "state", "trace", "proof", "cert"),
        optional_layers=("surface", "edges", "branch", "overlay", "projection", "dict"),
        provenance=provenance,
    )
    (out / "manifest.json").write_text(
        json.dumps(wrap_row(manifest.to_canonical_dict()), ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    result = EuIngestResult(
        work_id=work_id,
        celex=celex,
        out_dir=str(out),
        pack_id=manifest.pack_id,
        n_nodes=len(nodes),
        n_articles=n_articles,
        n_parags=n_parags,
        n_divisions=n_divisions,
        n_content_leaves=len(content_leaf_hashes),
        n_selection_rows=len(selection_row_hashes),
        n_residuals=n_residuals,
    )
    return result, work


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _witness_hash(nodes: Sequence[EuStructNode]) -> str:
    payload = [[n.identifier, n.text] for n in nodes]
    return leaf_hash("eu_ingest_witness", cast(JsonValue, payload))


def _build_certificate(
    *, work_id: str, materialization_root: str, selection_index_root: str, n_residuals: int
) -> tuple[str, dict[str, JsonValue]]:
    subroots = [materialization_root, selection_index_root]
    certificate_root = set_root("certificate", subroots)
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_CERTIFICATE,
        "work_id": work_id,
        "materialization_root": materialization_root,
        "selection_index_root": selection_index_root,
        "certificate_root": certificate_root,
        "residual_count": n_residuals,
        "certification_status": "clean" if n_residuals == 0 else "qualified",
    }
    return certificate_root, body


def _build_layer_descriptors(writers: dict[str, _LayerWriter]) -> tuple[Any, ...]:
    from lawvm.substrate.manifest import PackLayer

    descriptors: list[PackLayer] = []
    for kind in ("base", "state", "trace", "proof"):
        w = writers[kind]
        descriptors.append(
            PackLayer(
                kind=kind,
                path=f"{kind}/{kind}.jsonl",
                row_schema=f"lawvm.layer.{kind}.v0",
                codec=STORAGE_CODEC,
                dict_id="",
                uncompressed_sha256=w.uncompressed_sha256(),
                storage_sha256=w.uncompressed_sha256(),
                root=w.root(kind),
                root_fn=w.root_fn,
                row_count=w.row_count,
            )
        )
    return tuple(descriptors)


# --------------------------------------------------------------------------- #
# Snapshot pack reader (for check-pack on an EU work pack)                      #
# --------------------------------------------------------------------------- #

_EU_KNOWN_SCHEMAS = frozenset(
    {
        "lawvm.work.v1",
        SCHEMA_ADDRESS_NODE,
        SCHEMA_CONTENT_LEAF,
        SCHEMA_NODE_VERSION,
        SCHEMA_CERTIFICATE,
        "lawvm.residual.v1",
        "lawvm.coverage_row.v1",
        "lawvm.selection_row.v1",
        "lawvm.applicability_fact.v1",
        "lawvm.selection_candidate_set.v1",
        "lawvm.scope_predicate.v1",
        "lawvm.selection_profile.v1",
        "lawvm.selection_universe.v1",
        "lawvm.initial_state_event.v1",
        "lawvm.source_record.v1",
        "lawvm.source_manifestation.v1",
        "lawvm.legal_relation_edge.v0",
    }
)


def load_eu_pack_for_check(pack_dir: str | Path) -> Any:
    """Read an EU regulation pack back into a checker :class:`Pack`.

    Reuses the exporter's manifest reconstruction + the substrate ``Pack`` shape
    verbatim and supplies the EU known-schema set (which includes the
    source-lineage objects + the relation edge schema this producer may emit).
    """
    from lawvm.substrate.checker import Pack, PackLayerData
    from lawvm.substrate.exporter import _manifest_from_body

    pack_path = Path(pack_dir)
    manifest_row = json.loads((pack_path / "manifest.json").read_text(encoding="utf-8"))
    manifest_body = manifest_row["object"] if "object" in manifest_row else manifest_row
    manifest = _manifest_from_body(manifest_body)

    layers: dict[str, PackLayerData] = {}
    for layer in manifest.layers:
        rows_out: list[dict[str, JsonValue]] = []
        layer_file = pack_path / layer.path
        if layer_file.exists():
            with layer_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rows_out.append(json.loads(line))
        layers[layer.kind] = PackLayerData(
            kind=layer.kind,
            domain=layer.kind,
            root_fn=layer.root_fn,
            root=layer.root,
            rows=tuple(rows_out),
        )

    selection_universe: dict[str, str] | None = None
    selection_universe_root: str | None = None
    state = layers.get("state")
    if state is not None:
        universe_keys: dict[str, str] = {}
        for row in state.rows:
            body = row.get("object")
            if not isinstance(body, dict):
                continue
            typed_body = cast("dict[str, Any]", body)
            if typed_body.get("schema") == "lawvm.selection_row.v1":
                key = typed_body.get("selection_key")
                if isinstance(key, str):
                    universe_keys[key] = str(row["object_hash"])
        if universe_keys:
            selection_universe = universe_keys
            selection_universe_root = map_root("selection_universe", universe_keys)

    return Pack(
        manifest=manifest,
        layers=layers,
        selection_universe=selection_universe,
        selection_universe_root=selection_universe_root,
        referenced_hashes={},
        known_schemas=_EU_KNOWN_SCHEMAS,
    )


__all__ = [
    "EuStructNode",
    "IngestedEuWork",
    "TargetResolution",
    "EdgeResolution",
    "EuIngestResult",
    "FiResolveResult",
    "parse_consolidated_formex",
    "export_eu_regulation_pack",
    "parse_fi_eu_target",
    "resolve_fi_eu_target",
    "resolve_fi_eu_edge",
    "load_eu_pack_for_check",
]
