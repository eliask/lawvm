"""backrefs — the unresolved back-reference / anaphora recognizer family.

The recognizer family for the anaphoric back-reference a Finnish amendment verb
group lists *after* a complete prior target batch, pointing at the
last-mentioned section(s) without re-naming them:

  * ``mainitun pykälän [sub_ref ...]`` / ``mainittu pykälä`` — singular
    (``mainitun`` / ``mainittu``).
  * ``mainittujen pykälien [sub_ref ...]`` / ``mainitut pykälät`` — plural.

It emits one frozen ``SurfaceBackRef`` byte-identically to the old
``surface_parse`` driver's BACKREF continuation arm (the node-emitting site, NOT
the inline ``§:n numero N:ksi ja mainitun pykälän …`` renumber-tail site, which
the section family already reproduces as a ``renumber_backref_clause``
``SurfaceTargetRef``).  A ``SurfaceBackRef`` records the anaphor's arity and its
sub-references; it does NOT resolve which preceding section it refers to — that
is deferred to a post-parse pass.

Two enforced layers (per the rewrite contract):

  * a LOUD recognizer — a pure function over a ``_Scan`` cursor returning a
    structured intermediate (``ParsedBackRef``) carrying the span, the arity, and
    the raw sub-references.  Built on the same scanner substrate as the section
    family (it reuses ``_sub_ref`` / ``_sep`` verbatim); no frozen-node
    construction.
  * a thin emitter (``emit_backref_nodes``) turning the intermediate into the
    single frozen ``SurfaceBackRef`` node with its witness.

This is an ENTANGLED tail family.  ``SurfaceBackRef`` is only ever emitted as a
CONTINUATION arm inside a verb group's target list, never standalone: the old
parser does not read a leading bare ``mainitun pykälän …`` as a target (such a
clause parses to an empty verb group).  The recognizer entry is therefore the
``BACKREF`` token AFTER the driver has consumed the preceding list separator; the
witness span START (which the old parser anchors at the loop-iteration position,
i.e. BEFORE that separator) is owned by the driver, exactly as the heading
``VALIOTSIKKO`` backref's span is — see ``recognize_valiotsikko_ref`` and the
validation driver, which records ``saved`` before the separator and rewrites the
span.  Full-clause parity is thus a driver-integration concern; here the family
is validated at the recognizer/helper level against the old parser's helpers.

Determiner-anaphor INSERT arms (``sanottuun/mainittuun pykälään uusi …``) and
provenance anaphora (``mainitussa … asetuksessa``) are NOT this family — they
emit insertions / are skipped as provenance spans, respectively, and are out of
scope here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lawvm.finland.johtolause.grammar.combinators import Span, cat
from lawvm.finland.johtolause.grammar.sections import (
    SubRef,
    _Scan,
    _read,
    _sep,
    _sub_ref,
    _to_surface_sub_refs,
)
from lawvm.finland.johtolause.surface_model import (
    BackRefArity,
    SurfaceBackRef,
    SurfaceNode,
    SurfaceWitness,
)

# ---------------------------------------------------------------------------
# Atomic token matchers.
# ---------------------------------------------------------------------------
_BACKREF = cat("BACKREF")
_PYKALA = cat("PYKALA")

# The singular anaphoric determiners (faithful to the old driver's
# ``t2.text.lower() in ("mainitun", "mainittu")`` arity test).  Every other
# BACKREF determiner (``mainittujen`` / ``mainitut``) is plural.
_SINGULAR_DETERMINERS = frozenset({"mainitun", "mainittu"})


# ---------------------------------------------------------------------------
# Recognized back-reference (the intermediate the emitter consumes).
# ---------------------------------------------------------------------------


class BackRefForm(Enum):
    """Which back-reference production matched."""

    SINGULAR = "singular"  # mainitun pykälän … / mainittu pykälä
    PLURAL = "plural"  # mainittujen pykälien … / mainitut pykälät


_FORM_RULE_ID: dict["BackRefForm", str] = {
    BackRefForm.SINGULAR: "fi.backref_singular",
    BackRefForm.PLURAL: "fi.backref_plural",
}

_FORM_ARITY: dict["BackRefForm", BackRefArity] = {
    BackRefForm.SINGULAR: BackRefArity.SINGULAR,
    BackRefForm.PLURAL: BackRefArity.PLURAL,
}


@dataclass(frozen=True, slots=True)
class ParsedBackRef:
    """A recognized back-reference: form + span + raw sub-references.

    Architecture-neutral: carries only what the recognizer saw — the matched
    span, the arity (from the BACKREF determiner), and the sub-reference list
    (empty-singleton ``SubRef()`` meaning whole section).  The emitter turns this
    into the single frozen ``SurfaceBackRef`` node.
    """

    form: BackRefForm
    span: Span
    subs: tuple[SubRef, ...] = ()


def backref_rule_id(parsed: ParsedBackRef) -> str:
    """The witness ``rule_id`` the emitter attaches for a recognized backref."""
    return _FORM_RULE_ID[parsed.form]


# ---------------------------------------------------------------------------
# Recognizer (pure function over the cursor; None rewinds).
# ---------------------------------------------------------------------------


def recognize_backref(scan: _Scan, chapter: str = "", part: str = "") -> Optional[ParsedBackRef]:
    """Recognize ``BACKREF PYKALA [sub_ref ...]`` — an anaphoric back-reference.

    Faithful to ``surface_parse._parse_backref_continuation`` plus the driver's
    arity test: the BACKREF determiner fixes the arity (``mainitun`` /
    ``mainittu`` = singular, else plural), ``pykälän``/``pykälä``/``pykälien``/
    ``pykälät`` is consumed, then the trailing sub-references (with the same
    kohta-level trailing-facet distribution the section path applies).  An absent
    sub-reference becomes a single whole-section ``SubRef()``.

    The recognizer entry is the ``BACKREF`` token; any preceding list separator
    is the driver's to consume (and to fold into the witness span START), exactly
    as the heading ``VALIOTSIKKO`` backref — see the validation driver, which
    records ``saved`` before the separator and rewrites the span.  Returns
    ``None`` (rewinding ``scan``) on no match.
    """
    start = scan.pos
    det = _read(scan, _BACKREF)
    if det is None:
        return None
    is_singular = (det.text or "").lower() in _SINGULAR_DETERMINERS
    if _read(scan, _PYKALA) is None:
        scan.goto(start)
        return None

    subs = _sub_ref(scan)
    # Additional sub-refs joined by separators (faithful to the old
    # continuation's separator-joined sub-ref loop).
    if subs:
        while True:
            saved2 = scan.pos
            if _sep(scan) is None:
                break
            more = _sub_ref(scan)
            if more:
                subs.extend(more)
            else:
                scan.goto(saved2)
                break
    if not subs:
        subs = [SubRef()]  # whole section ("mainittu pykälä")

    subs = _distribute_trailing_kohta_facet(subs)

    form = BackRefForm.SINGULAR if is_singular else BackRefForm.PLURAL
    return ParsedBackRef(form=form, span=Span(start, scan.pos), subs=tuple(subs))


def _distribute_trailing_kohta_facet(subs: list[SubRef]) -> list[SubRef]:
    """Distribute a trailing kohta-level facet over preceding same-depth arms.

    Faithful to the tail of ``_parse_backref_continuation`` (the same logic the
    section path applies): when the last sub-ref carries both a facet and an
    item, that facet is copied onto every preceding facet-less item-bearing arm.
    """
    if len(subs) > 1 and subs[-1].facet is not None and subs[-1].item:
        trailing_facet = subs[-1].facet
        return [
            SubRef(momentti=sr.momentti, item=sr.item, facet=trailing_facet)
            if i < len(subs) - 1 and sr.facet is None and sr.item
            else sr
            for i, sr in enumerate(subs)
        ]
    return subs


# ---------------------------------------------------------------------------
# Emitter — ParsedBackRef -> the frozen SurfaceBackRef node.
# ---------------------------------------------------------------------------


def emit_backref_nodes(
    parsed: ParsedBackRef, chapter: str = "", part: str = ""
) -> list[SurfaceNode]:
    """Turn a recognized ``ParsedBackRef`` into the frozen ``SurfaceBackRef``.

    ``chapter`` / ``part`` are accepted for emitter-signature uniformity with the
    other families; a ``SurfaceBackRef`` carries no scope context (resolution is
    deferred), so they are unused here.
    """
    return [
        SurfaceBackRef(
            referent_type=_FORM_ARITY[parsed.form],
            sub_refs=_to_surface_sub_refs(list(parsed.subs)),
            witness=SurfaceWitness(
                rule_id=backref_rule_id(parsed),
                source_span=(parsed.span.start, parsed.span.end),
            ),
        )
    ]


__all__ = [
    "BackRefForm",
    "ParsedBackRef",
    "backref_rule_id",
    "emit_backref_nodes",
    "recognize_backref",
]
