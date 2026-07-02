"""Canonical Text-State Form (CTSF) — task #184.

A *constructive whitelist* successor to the neutralizer blacklist
(``_section_diff_is_bench_neutralized`` and the ad-hoc "ignore whitespace /
dot-leaders / RetainText / 'aiempi sanamuoto'" rules).

Design source: ``notes_internal/FABLE_CORRECTNESS_METRIC.md`` (§1) and
``notes_internal/pro_on_fable_notes.txt``.

Concept
-------
Normative-for-replay content is *exactly* what the amendment grammar can
address, read, or write: unit **labels**, unit **kind**, **hierarchy /
containment** (child order), **occupancy state** (live/repealed/…), and
grammar-normalized **wording** and **heading**.  Everything a node carries that
is not one of those is editorial *by construction*.

The blacklist proves each artifact ignorable (open-ended; a bug-masking hole per
campaign).  CTSF inverts the burden of proof: it *builds* the addressable form
and declares everything outside it editorial.  Anything a node carries that is
not representable in CTSF v0 (complex tables, non-textual attachments,
format-sensitive layout) becomes a **typed residual** ``CNF_UNSUPPORTED_<kind>``
— never a silent drop.

DISCLAIMER (carried in code and docs, per the Fable rename ruling):

    CTSF-equality is the equality relation for LawVM replay text-state claims;
    it is NOT a claim that discarded presentation can never matter legally.

Scope of v0
-----------
* Constructive projection ``to_ctsf`` over ``SemanticStructureNode`` (the
  logical legal IR — *not* raw visual layout).
* The control-pair **admission gate** (``ctsf_admission_gate.py``): no editorial
  rule may enter CTSF without its four control-pair obligations.
* A parallel, READ-ONLY telemetry surface (``ctsf_equal`` + residual inventory).
  This module is *not* wired into the bench headline or any gate: importing it
  must leave default bench output byte-identical.

Out of scope for v0 (follow-ups, not built here): the full ``STATE_INDEX.*``
commensurability layer, migrating *all* neutralizer rules, and making CTSF the
gating metric.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Literal

from lawvm.core.regex_safety import compile_classifier_regex
from lawvm.semantic.diff import (
    _is_editorial_or_empty_shell,
    _is_repeal_indicator,
    _normalize_heading_for_diff,
    _normalize_wording_for_diff,
)
from lawvm.semantic.model import (
    SemanticStructureNode,
    _node_wording_facet,
    is_semantic_facet_kind,
)

CTSF_VERSION = "v0"

# "Aiempi sanamuoto kuuluu:" — Finlex's former-wording banner: the block from
# the marker onward is the SUPERSEDED prior wording, an editorial escape hatch
# a 1-D consolidation uses to show what the text used to say.  No amendment
# addresses it (§1.1); elide from the marker to end of the wording facet.
_AIEMPI_SANAMUOTO_MARKER_RE = compile_classifier_regex(
    r"\s*Aiempi sanamuoto kuuluu:.*$",
    re.IGNORECASE | re.DOTALL,
    classifier_id="ctsf.aiempi_sanamuoto_marker",
)

# Momentti ordinal prefix — Finlex renders a momentti's wording with its own
# ordinal prefixed ("1. <text>"); LawVM carries that number in the label/badge
# only.  The prefix is label-redundant presentation (§1.1: the label is addressed
# separately by the grammar), so a wording whose leading "N." equals the unit's
# own label has that prefix elided.  Label-aware match built per node.
_MOMENTTI_ORDINAL_PREFIX_RE = compile_classifier_regex(
    r"^\s*(\d+)\s*a?\.\s+",
    re.IGNORECASE,
    classifier_id="ctsf.momentti_ordinal_prefix",
)

# Digit-item prefix — a flat Finlex digit-item ("N) <text>") whose N duplicates
# the item's own label.  LawVM renests these into merged item children carrying
# the number in the label only (the flat→merged digit-renesting encoding).  The
# "N) " prefix is label-redundant (the item label is addressed separately), so it
# is elided when N equals the unit's own label.  Label-aware match built per node.
_DIGIT_ITEM_PREFIX_RE = compile_classifier_regex(
    r"^\s*(\d+[a-zäöå]?)\)\s+",
    re.IGNORECASE,
    classifier_id="ctsf.digit_item_prefix",
)

# The disclaimer, exported so telemetry surfaces and docs quote it verbatim.
CTSF_EQUALITY_DISCLAIMER = (
    "CTSF-equality is the equality relation for LawVM replay text-state claims; "
    "it is NOT a claim that discarded presentation can never matter legally."
)

# ---------------------------------------------------------------------------
# Occupancy state — the amendment-grammar-addressable occupancy field (§1.2).
# ---------------------------------------------------------------------------

OccupancyState = Literal["live", "repealed", "empty_shell"]


# ---------------------------------------------------------------------------
# Typed residuals — CNF_UNSUPPORTED_<kind>.  A node carrying something CTSF v0
# cannot represent addressably emits one of these instead of silently dropping.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CTSFResidual:
    """A typed 'CTSF v0 cannot represent this addressably' marker.

    ``kind`` is a ``CNF_UNSUPPORTED_<CONSTRUCT>`` token.  ``witness`` is a
    content-hash + snippet so a reviewer can confirm the projection did not eat a
    real divergence (witnessed elision, §1.3(4)).
    """

    kind: str
    address: str
    witness: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        item = {"kind": self.kind, "address": self.address, "witness": self.witness}
        if self.detail:
            item["detail"] = self.detail
        return item


def _residual_kind(construct: str) -> str:
    return f"CNF_UNSUPPORTED_{construct.upper()}"


# ---------------------------------------------------------------------------
# Projection witnesses — every elided editorial fragment emits an auditable
# witness (hash + snippet), so losslessness = the audit trail retains a section
# of the fiber (§1.3(4)).
# ---------------------------------------------------------------------------


def ctsf_witness(text: str) -> str:
    """Content hash + leading snippet for an elided fragment (self-evidencing)."""
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    snippet = text[:60].replace("\n", " ")
    return f"{digest}/{snippet!r}"


@dataclass(frozen=True, slots=True)
class CTSFElisionWitness:
    """One witnessed editorial elision, tagged with the rule that discarded it."""

    rule_id: str
    address: str
    witness: str

    def to_dict(self) -> dict[str, str]:
        return {"rule_id": self.rule_id, "address": self.address, "witness": self.witness}


# ---------------------------------------------------------------------------
# The CTSF node — the constructive whitelist.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CTSFNode:
    """The canonical text-state form of one addressable unit.

    Fields are *exactly* what the amendment grammar can address/read/write:

    * ``address`` — the addressing path (kind:label chain from the root).
    * ``kind`` — unit kind (section/subsection/item/…): amendments name it.
    * ``label`` — identity label ("111", "2"): amendments target it.
    * ``occupancy_state`` — live/repealed/empty_shell: repeal presupposes a live
      target; the grammar reads and writes occupancy.
    * ``normalized_heading`` — grammar-normalized heading facet.
    * ``normalized_text`` — grammar-normalized wording (the quoted-span matcher's
      own normalization defines text equality, §1.1 corollary).
    * ``child_order`` — the ordered child CTSF nodes; containment is normative
      because addressing paths traverse it.

    Anything a node carried that is not one of the above is either an editorial
    elision (witnessed) or a typed ``CNF_UNSUPPORTED_*`` residual.
    """

    address: str
    kind: str
    label: str
    occupancy_state: OccupancyState
    normalized_heading: str
    normalized_text: str
    child_order: tuple["CTSFNode", ...] = ()
    residuals: tuple[CTSFResidual, ...] = ()
    elisions: tuple[CTSFElisionWitness, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "address": self.address,
            "kind": self.kind,
            "label": self.label,
            "occupancy_state": self.occupancy_state,
        }
        if self.normalized_heading:
            item["normalized_heading"] = self.normalized_heading
        if self.normalized_text:
            item["normalized_text"] = self.normalized_text
        if self.child_order:
            item["child_order"] = [c.to_dict() for c in self.child_order]
        if self.residuals:
            item["residuals"] = [r.to_dict() for r in self.residuals]
        if self.elisions:
            item["elisions"] = [e.to_dict() for e in self.elisions]
        return item

    # -- CTSF-equality: exactly the addressable fields; residuals/elisions are
    # audit metadata and never participate in the equality relation. --
    def _equality_key(self) -> tuple[object, ...]:
        return (
            self.kind,
            self.label,
            self.occupancy_state,
            self.normalized_heading,
            self.normalized_text,
            tuple(c._equality_key() for c in self.child_order),
        )


def _occupancy_of(node: SemanticStructureNode) -> OccupancyState:
    if _is_repeal_indicator(node):
        return "repealed"
    if _is_editorial_or_empty_shell(node):
        # An empty shell whose repeal is explicitly marked is repealed; an
        # untyped empty shell is distinguished from live so occupancy equality is
        # meaningful, but repeal is not asserted without a basis.
        if node.label_basis in ("editorial_repeal_notice", "repeal_placeholder"):
            return "repealed"
        return "empty_shell"
    return "live"


def _addressable_child(child: SemanticStructureNode) -> bool:
    # Facet-kind children (heading/intro/wrapUp) are folded into the parent's
    # heading/text; they are not addressable *units* of their own.
    return not is_semantic_facet_kind(child.kind)


def _elide_label_redundant_prefix(
    text: str,
    label: str,
    address: str,
    elisions: list[CTSFElisionWitness],
) -> str:
    """Elide a leading ordinal / digit-item prefix that duplicates ``label``.

    One-sided and label-aware: strips a leading ``"N. "`` (momentti ordinal) or
    ``"N) "`` (flat digit-item) prefix ONLY when ``N`` equals the unit's own
    ``label``.  The label is compared separately, so a label-redundant leading
    number is presentation; a leading number that does NOT match the label
    (a genuine date/list value) is preserved verbatim.  Emits a witness when it
    strips.  Realizes two migrated rules: ``ctsf.text.momentti_ordinal_elision``
    (from ``_strip_momentti_ordinal_prefix``) and
    ``ctsf.structure.digit_item_renesting_elision`` (from
    ``_is_digit_renesting_mismatch``'s ``N)`` prefix strip).
    """
    if not label:
        return text
    for pattern, rule_id in (
        (_MOMENTTI_ORDINAL_PREFIX_RE, "ctsf.text.momentti_ordinal_elision"),
        (_DIGIT_ITEM_PREFIX_RE, "ctsf.structure.digit_item_renesting_elision"),
    ):
        m = pattern.match(text)
        if m and m.group(1).lower() == label.lower():
            elisions.append(
                CTSFElisionWitness(
                    rule_id=rule_id,
                    address=address,
                    witness=ctsf_witness(text),
                )
            )
            return text[m.end():]
    return text


def to_ctsf(node: SemanticStructureNode, *, _path: str = "") -> CTSFNode:
    """Project a logical IR node into its Canonical Text-State Form.

    One-sided (computed from ``node`` alone, never from a pair) and idempotent on
    its addressable fields.  Editorial content is elided-with-witness; any
    construct CTSF v0 cannot represent addressably becomes a typed
    ``CNF_UNSUPPORTED_*`` residual rather than a silent drop.
    """
    label = "" if node.label.startswith("__") else node.label
    address = f"{_path}/{node.kind}:{label}" if _path else f"{node.kind}:{label}"

    residuals: list[CTSFResidual] = []
    elisions: list[CTSFElisionWitness] = []
    occupancy = _occupancy_of(node)

    # -- heading (grammar-normalized) --
    heading_facet = next((f for f in node.facets if f.kind == "heading"), None)
    normalized_heading = ""
    if heading_facet is not None and heading_facet.text:
        normalized_heading = _normalize_heading_for_diff(heading_facet.text)
        if normalized_heading != heading_facet.text:
            elisions.append(
                CTSFElisionWitness(
                    rule_id="ctsf.text.grammar_normalization",
                    address=address,
                    witness=ctsf_witness(heading_facet.text),
                )
            )

    # -- wording (grammar-normalized) --
    wording_facet = _node_wording_facet(node)
    raw_wording = ""
    if wording_facet is not None and wording_facet.text:
        raw_wording = wording_facet.text
    elif node.text:
        raw_wording = node.text
    normalized_text = ""
    if raw_wording and occupancy == "repealed":
        # ctsf.occupancy.repeal_tombstone_elision — a repealed unit's residual
        # wording is a consolidation tombstone banner / RetainText retained
        # phrase / "Aiempi sanamuoto kuuluu:" former-wording block.  None of it
        # is addressable (no amendment reads a repealed unit's tombstone text);
        # occupancy=repealed is the whole normative content.  Elide with witness.
        elisions.append(
            CTSFElisionWitness(
                rule_id="ctsf.occupancy.repeal_tombstone_elision",
                address=address,
                witness=ctsf_witness(raw_wording),
            )
        )
    elif raw_wording:
        # ctsf.text.aiempi_sanamuoto_elision — strip the former-wording banner
        # (from the marker to end) before normalization; it is not addressable.
        stripped = _AIEMPI_SANAMUOTO_MARKER_RE.sub("", raw_wording)
        if stripped != raw_wording:
            elisions.append(
                CTSFElisionWitness(
                    rule_id="ctsf.text.aiempi_sanamuoto_elision",
                    address=address,
                    witness=ctsf_witness(raw_wording),
                )
            )
        # label-redundant ordinal elision — elide a leading ordinal that
        # DUPLICATES the unit's own label: a momentti "N. " prefix (Finlex ordinal
        # rendering, ctsf.text.momentti_ordinal_elision) or a flat digit-item
        # "N) " prefix (the flat→merged digit-renesting encoding,
        # ctsf.structure.digit_item_renesting_elision).  The label is addressed
        # separately by the
        # grammar (§1.1), so a leading number equal to the label is presentation.
        # Label-aware: elide ONLY when the leading N equals node.label, so a
        # genuine leading number ("1. tammikuuta", "3) muu asia" under a
        # differently-numbered unit) is never masked.
        stripped = _elide_label_redundant_prefix(stripped, node.label, address, elisions)
        normalized_text = _normalize_wording_for_diff(stripped, node.label)
        if normalized_text != stripped:
            elisions.append(
                CTSFElisionWitness(
                    rule_id="ctsf.text.grammar_normalization",
                    address=address,
                    witness=ctsf_witness(stripped),
                )
            )

    # -- typed residuals for constructs CTSF v0 cannot address --
    residuals.extend(_typed_residuals(node, address))

    # -- children in order (containment is normative) --
    child_nodes: list[CTSFNode] = []
    for child in node.children:
        if not _addressable_child(child):
            continue
        child_nodes.append(to_ctsf(child, _path=address))

    return CTSFNode(
        address=address,
        kind=node.kind,
        label=label,
        occupancy_state=occupancy,
        normalized_heading=normalized_heading,
        normalized_text=normalized_text,
        child_order=tuple(child_nodes),
        residuals=tuple(residuals),
        elisions=tuple(elisions),
    )


def _typed_residuals(node: SemanticStructureNode, address: str) -> list[CTSFResidual]:
    """Emit ``CNF_UNSUPPORTED_*`` residuals for constructs v0 cannot represent.

    Applied to the LOGICAL IR: physical layout is evidence, logical table/row/col
    structure is legal, cell text is text-state.  CTSF v0 does not yet model
    logical table structure addressably, so a table-bearing wording facet emits a
    typed residual rather than pretending the flat wording captured it.
    """
    out: list[CTSFResidual] = []
    for facet in node.facets:
        for table in getattr(facet, "tables", ()):  # SemanticStructureFacet.tables
            witness_src = getattr(table, "table_id", "") or getattr(table, "caption", "")
            out.append(
                CTSFResidual(
                    kind=_residual_kind("TABLE"),
                    address=address,
                    witness=ctsf_witness(str(witness_src) or "table"),
                    detail="logical table structure not addressable in CTSF v0",
                )
            )
    return out


# ---------------------------------------------------------------------------
# The parallel metric surface — READ-ONLY telemetry (§5).  Never wired into the
# bench headline or any gate in v0.
# ---------------------------------------------------------------------------


def ctsf_equal(left: CTSFNode, right: CTSFNode) -> bool:
    """CTSF-equality: equality of the addressable fields only.

    Residuals and elision witnesses are audit metadata and do not participate.
    """
    return left._equality_key() == right._equality_key()


def collect_residuals(node: CTSFNode) -> tuple[CTSFResidual, ...]:
    out: list[CTSFResidual] = list(node.residuals)
    for child in node.child_order:
        out.extend(collect_residuals(child))
    return tuple(out)


def collect_elisions(node: CTSFNode) -> tuple[CTSFElisionWitness, ...]:
    out: list[CTSFElisionWitness] = list(node.elisions)
    for child in node.child_order:
        out.extend(collect_elisions(child))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class CTSFTelemetry:
    """Read-only per-statute CTSF comparison report.

    NOT a score and NOT a gate — a diagnostic surface reporting CTSF-equality
    plus the typed residual inventory of both sides.
    """

    ctsf_equal: bool
    replay_residuals: tuple[CTSFResidual, ...]
    oracle_residuals: tuple[CTSFResidual, ...]
    replay_elisions: tuple[CTSFElisionWitness, ...]
    oracle_elisions: tuple[CTSFElisionWitness, ...]
    disclaimer: str = CTSF_EQUALITY_DISCLAIMER

    def residual_inventory(self) -> dict[str, int]:
        inv: dict[str, int] = {}
        for r in (*self.replay_residuals, *self.oracle_residuals):
            inv[r.kind] = inv.get(r.kind, 0) + 1
        return inv

    def to_dict(self) -> dict[str, Any]:
        return {
            "ctsf_version": CTSF_VERSION,
            "ctsf_equal": self.ctsf_equal,
            "residual_inventory": self.residual_inventory(),
            "replay_residuals": [r.to_dict() for r in self.replay_residuals],
            "oracle_residuals": [r.to_dict() for r in self.oracle_residuals],
            "replay_elisions": [e.to_dict() for e in self.replay_elisions],
            "oracle_elisions": [e.to_dict() for e in self.oracle_elisions],
            "disclaimer": self.disclaimer,
        }


def ctsf_telemetry(
    replay: SemanticStructureNode | None,
    oracle: SemanticStructureNode | None,
) -> CTSFTelemetry:
    """Project BOTH sides to CTSF and report equality + typed residual inventory.

    Read-only telemetry: computing it changes nothing about replay, the oracle,
    or the bench headline.
    """
    replay_ctsf = to_ctsf(replay) if replay is not None else None
    oracle_ctsf = to_ctsf(oracle) if oracle is not None else None
    if replay_ctsf is None or oracle_ctsf is None:
        equal = replay_ctsf is None and oracle_ctsf is None
    else:
        equal = ctsf_equal(replay_ctsf, oracle_ctsf)
    return CTSFTelemetry(
        ctsf_equal=equal,
        replay_residuals=collect_residuals(replay_ctsf) if replay_ctsf else (),
        oracle_residuals=collect_residuals(oracle_ctsf) if oracle_ctsf else (),
        replay_elisions=collect_elisions(replay_ctsf) if replay_ctsf else (),
        oracle_elisions=collect_elisions(oracle_ctsf) if oracle_ctsf else (),
    )
