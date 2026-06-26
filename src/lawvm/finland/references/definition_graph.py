"""Finnish statute DEFINITION GRAPH assembler + the first LawVM legal LINT.

This module is the H2 half of the defined-term machinery.  Its two inputs
already exist on master and are imported READ-ONLY:

  * :mod:`lawvm.finland.references.defined_terms` — the BINDER: recognises where
    a local term is introduced and tied to a target (the definition NODES).
  * :mod:`lawvm.finland.references.term_use` — the USE resolver: finds later
    uses of those terms and resolves each back to a binding (the use NODES + the
    binding↔use EDGES).

:func:`build_definition_graph` decodes the statute body text, runs both passes,
assembles a frozen :class:`DefinitionGraph` (definition nodes, use nodes,
resolved edges), and then computes typed :class:`Lint` records.

The lints are the payoff — LawVM's FIRST legal static-analysis lint, in the
spirit of the Legal Surface Algebra vision: a static analyzer for law that
reports SURFACE / STRUCTURAL facts, never legal conclusions.  "Term X is used
but never defined in this statute" is a surface fact about the document's own
definitional structure; it is NOT a claim that the statute is invalid, wrong, or
that any legal consequence follows.

Lint kinds (closed set):

  * ``UNBOUND_TERM``          — a use whose status is ``open``: a term used that
                                matches a binding's surface but has no in-scope
                                binding (used before / without a definition).
  * ``USED_BEFORE_DEFINITION``— a use that matches a binding positioned AFTER it
                                (the order-violation subset of ``open``).
  * ``DEFINITION_NEVER_USED`` — a binding with zero resolved uses (dead
                                definition).
  * ``DUPLICATE_DEFINITION``  — the same term bound more than once (all listed).
  * ``AMBIGUOUS_TERM_USE``    — a use matching more than one in-scope binding.

Fail-loud discipline (AGENTS.md §1.8): every lint is typed and carries the span
of the offending construct, and every lint ``message`` is SELF-EVIDENCING — it
embeds the offending term and/or surface text so the finding is auditable from
the message alone, never an opaque code.  Nothing is silently dropped: every use
that is ``open``/``ambiguous`` and every unused/duplicate binding produces a
lint.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.references.defined_terms import (
    DefinedTermBinding,
    recognize_defined_term_bindings,
)
from lawvm.finland.references.term_use import (
    STATUS_AMBIGUOUS,
    STATUS_OPEN,
    STATUS_RESOLVED,
    TermUse,
    resolve_term_uses,
)

# ---------------------------------------------------------------------------
# Lint kinds / severities (closed sets)
# ---------------------------------------------------------------------------

LINT_UNBOUND_TERM = "UNBOUND_TERM"
LINT_USED_BEFORE_DEFINITION = "USED_BEFORE_DEFINITION"
LINT_DEFINITION_NEVER_USED = "DEFINITION_NEVER_USED"
LINT_DUPLICATE_DEFINITION = "DUPLICATE_DEFINITION"
LINT_AMBIGUOUS_TERM_USE = "AMBIGUOUS_TERM_USE"

#: severity values (closed set).  These rank the surface signal, NOT legal
#: importance: an ``error`` is a structural inconsistency (a use with no usable
#: binding, or two conflicting bindings); a ``warning`` is a smell (a dead
#: definition).  No severity implies a legal conclusion.
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


# ---------------------------------------------------------------------------
# Typed output
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Lint:
    """A single surface/structural finding about the statute's definitions.

    Attributes:
        kind:        One of the ``LINT_*`` constants.
        severity:    ``"error"`` or ``"warning"`` (surface signal rank, not a
                     legal conclusion).
        message:     SELF-EVIDENCING human-readable finding; embeds the offending
                     term and/or surface text so the lint is auditable from the
                     message alone (AGENTS.md §1.8 — never an opaque code).
        source_span: Byte range of the offending construct in the assembled body
                     text (a use token's span, or a binding's span).
        term:        The term the lint is about (binding term lemma, or the
                     surface of an unbound use).
    """

    kind: str
    severity: str
    message: str
    source_span: SourceSpan
    term: str


@dataclass(frozen=True, slots=True)
class DefinitionEdge:
    """A resolved binding↔use edge: a use that resolves to exactly one binding."""

    binding: DefinedTermBinding
    use: TermUse


@dataclass(frozen=True, slots=True)
class DefinitionGraph:
    """The assembled definition graph for one statute, plus its lints.

    Attributes:
        statute_id:  The statute the graph was built for.
        body_text:   The assembled plain-text body the passes ran over (spans in
                     bindings/uses/lints are byte offsets into THIS string).
        bindings:    Definition nodes — every recognised :class:`DefinedTermBinding`.
        uses:        Use nodes — every :class:`TermUse` (resolved/open/ambiguous).
        edges:       Resolved binding↔use edges (one per ``resolved`` use).
        lints:       Typed surface/structural findings (see :class:`Lint`).
    """

    statute_id: str
    body_text: str
    bindings: tuple[DefinedTermBinding, ...]
    uses: tuple[TermUse, ...]
    edges: tuple[DefinitionEdge, ...]
    lints: tuple[Lint, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Body-text extraction
# ---------------------------------------------------------------------------


def _local_name(tag: str) -> str:
    """Strip an XML namespace from a tag, returning the local name."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _extract_body_text(xml_bytes: bytes) -> str:
    """Decode the statute body text by joining ``<p>`` element text.

    Mirrors the other lanes: a tree walk (AGENTS.md §1.13), collecting
    ``itertext()`` over every ``<p>`` element (namespace-agnostic by local name)
    and joining paragraphs with newlines.  Newline joins keep paragraph
    boundaries so a sentence/clause window in a recognizer cannot bleed across
    unrelated paragraphs.

    Returns the empty string when the XML cannot be parsed or has no ``<p>``
    text — the caller then produces an empty graph (no bindings, no lints), never
    a crash.
    """
    if b"<p" not in xml_bytes and b":p" not in xml_bytes:
        return ""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""

    paragraphs: list[str] = []
    for el in root.iter():
        if _local_name(el.tag) != "p":
            continue
        text = "".join(el.itertext())
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


# ---------------------------------------------------------------------------
# Lint computation
# ---------------------------------------------------------------------------


def _snippet(text: str, span: SourceSpan, pad: int = 24) -> str:
    """Return a bounded, single-line context snippet around ``span``.

    Used to make a lint message self-evidencing by quoting the surrounding
    source text.  Whitespace is collapsed; the window is bounded so a message
    cannot explode on a long paragraph.
    """
    start = max(0, span.byte_offset - pad)
    end = min(len(text), span.byte_offset + span.byte_len + pad)
    frag = text[start:end].replace("\n", " ")
    return " ".join(frag.split())


def _compute_lints(
    body_text: str,
    bindings: tuple[DefinedTermBinding, ...],
    uses: tuple[TermUse, ...],
    edges: tuple[DefinitionEdge, ...],
    *,
    source_file: str,
) -> tuple[Lint, ...]:
    """Compute the typed lints from the assembled graph (see module docstring)."""
    lints: list[Lint] = []

    # -- Use-side lints: open / ambiguous uses --------------------------------
    #
    # The resolver tags a use ``open`` when it matched some binding's surface but
    # had NO in-scope binding (every matching binding lies after the use, or no
    # usable binding remains).  We split ``open`` into two typed kinds by the
    # POSITION of the matching binding(s):
    #
    #   * at least one matching binding starts AFTER the use's span end
    #       -> USED_BEFORE_DEFINITION  (a recoverable order violation: the term
    #          IS defined in the statute, just later than this use)
    #   * the term's surface matches a binding but none lies after the use
    #     (e.g. the term is named only as a definition expansion / target and is
    #     not actually bound at a usable position before this use)
    #       -> UNBOUND_TERM            (used, but no definition reachable in scope)
    #
    # The resolver empties ``bindings`` on an ``open`` use, so we recompute the
    # matching binding set here from the binder output (by surface == lemma) to
    # learn the positions — this never refers to a binding not produced by the
    # binder, so it cannot fabricate a definition (AGENTS.md §1.8).
    for use in uses:
        if use.use_status == STATUS_OPEN:
            lemma = use.lemma.strip().lower()
            surf = use.term_surface.strip().lower()
            matching = [
                b
                for b in bindings
                if b.term.strip().lower() in (lemma, surf)
            ]
            use_end = use.source_span.byte_offset + use.source_span.byte_len
            later = [b for b in matching if b.source_span.byte_offset >= use_end]
            if later:
                lints.append(
                    Lint(
                        kind=LINT_USED_BEFORE_DEFINITION,
                        severity=SEVERITY_ERROR,
                        message=(
                            f"term {use.term_surface!r} (definition {use.lemma!r}) "
                            f"is used before it is defined: …{_snippet(body_text, use.source_span)}…"
                        ),
                        source_span=use.source_span,
                        term=use.lemma,
                    )
                )
            else:
                lints.append(
                    Lint(
                        kind=LINT_UNBOUND_TERM,
                        severity=SEVERITY_ERROR,
                        message=(
                            f"term {use.term_surface!r} is used but has no "
                            f"definition reachable in scope: "
                            f"…{_snippet(body_text, use.source_span)}…"
                        ),
                        source_span=use.source_span,
                        term=use.term_surface,
                    )
                )
        elif use.use_status == STATUS_AMBIGUOUS:
            cand = ", ".join(sorted({b.term for b in use.bindings}))
            lints.append(
                Lint(
                    kind=LINT_AMBIGUOUS_TERM_USE,
                    severity=SEVERITY_ERROR,
                    message=(
                        f"term {use.term_surface!r} matches {len(use.bindings)} "
                        f"definitions ({cand}); cannot pick one: "
                        f"…{_snippet(body_text, use.source_span)}…"
                    ),
                    source_span=use.source_span,
                    term=use.lemma,
                )
            )

    # -- Definition-side lints: never used ------------------------------------
    used_binding_ids = {id(e.binding) for e in edges}
    for b in bindings:
        if id(b) not in used_binding_ids:
            lints.append(
                Lint(
                    kind=LINT_DEFINITION_NEVER_USED,
                    severity=SEVERITY_WARNING,
                    message=(
                        f"definition of {b.term!r} (target={b.target_ref!r}) is "
                        f"never used in this statute (dead definition): "
                        f"…{_snippet(body_text, b.source_span)}…"
                    ),
                    source_span=b.source_span,
                    term=b.term,
                )
            )

    # -- Definition-side lints: duplicates ------------------------------------
    by_term: dict[str, list[DefinedTermBinding]] = {}
    for b in bindings:
        by_term.setdefault(b.term.strip().lower(), []).append(b)
    for key, group in by_term.items():
        if len(group) <= 1:
            continue
        for b in group:
            offsets = sorted(g.source_span.byte_offset for g in group)
            lints.append(
                Lint(
                    kind=LINT_DUPLICATE_DEFINITION,
                    severity=SEVERITY_ERROR,
                    message=(
                        f"term {b.term!r} is defined {len(group)} times "
                        f"(at byte offsets {offsets}); this binding: "
                        f"…{_snippet(body_text, b.source_span)}…"
                    ),
                    source_span=b.source_span,
                    term=b.term,
                )
            )

    lints.sort(key=lambda li: (li.source_span.byte_offset, li.kind))
    return tuple(lints)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_definition_graph(
    xml_bytes: bytes,
    statute_id: str,
) -> DefinitionGraph:
    """Build the definition graph + lints for a Finnish statute.

    Pipeline:
      1. decode the body text from ``<p>`` elements (tree walk + ``itertext``),
      2. run the defined-term BINDER over it -> definition nodes,
      3. run the USE resolver -> use nodes (resolved/open/ambiguous),
      4. assemble the resolved binding↔use edges,
      5. compute the typed surface/structural lints.

    Args:
        xml_bytes:  Raw consolidated Finlex AKN XML bytes.
        statute_id: Statute id, e.g. ``"2014/527"`` (recorded on the graph and
                    used as the ``source_file`` of every span).

    Returns:
        A frozen :class:`DefinitionGraph`.  When the XML has no ``<p>`` text the
        graph is empty (no bindings, no uses, no lints) — never a crash.
    """
    body_text = _extract_body_text(xml_bytes)
    if not body_text:
        return DefinitionGraph(
            statute_id=statute_id,
            body_text="",
            bindings=(),
            uses=(),
            edges=(),
            lints=(),
        )

    bindings_list = recognize_defined_term_bindings(body_text, source_file=statute_id)
    uses_list = resolve_term_uses(body_text, bindings_list, source_file=statute_id)

    bindings = tuple(bindings_list)
    uses = tuple(uses_list)

    edges = tuple(
        DefinitionEdge(binding=u.binding, use=u)
        for u in uses
        if u.use_status == STATUS_RESOLVED and u.binding is not None
    )

    lints = _compute_lints(
        body_text, bindings, uses, edges, source_file=statute_id
    )

    return DefinitionGraph(
        statute_id=statute_id,
        body_text=body_text,
        bindings=bindings,
        uses=uses,
        edges=edges,
        lints=lints,
    )


__all__ = [
    "LINT_AMBIGUOUS_TERM_USE",
    "LINT_DEFINITION_NEVER_USED",
    "LINT_DUPLICATE_DEFINITION",
    "LINT_UNBOUND_TERM",
    "LINT_USED_BEFORE_DEFINITION",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "DefinitionEdge",
    "DefinitionGraph",
    "Lint",
    "build_definition_graph",
]
