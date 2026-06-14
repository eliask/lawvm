"""Token-coverage audit for the Finnish johtolause parser (loudness instrument).

The hand-written recursive-descent parser in ``surface_parse.py`` can advance
its stream position over tokens WITHOUT producing a node (e.g. the
"skip non-VERB tokens to find the next VERB" loop, or a partially-matched
verb group that then fails and ``continue``s).  Those spans are silently
dropped: ``consumed_count`` reaches the end, so the existing residual check in
``api.py`` never fires, and no diagnostic is emitted.  A real amendment clause
can therefore vanish with no trace (verified on amendment 2009/886, which lost
``69 j / 69 k / 69 l / 71 § / 138 §`` before that grammar gap was closed).

This module is a MEASURE-ONLY instrument: given the filtered token stream and
the parsed ``SurfaceClauseModel``, it computes which token indices are covered
by a produced node's witness span and reports every maximal run of non-sentinel
tokens that no node covers.  Each report carries the exact source text of the
unparsed span, so it is self-evidencing.

It deliberately does NOT touch the parse hot path.  Wiring its output into the
typed-diagnostic stream (so the parser becomes provably total) is a separate,
later step; first the instrument is validated and run corpus-wide to size the
silent-drop tail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from lawvm.finland.johtolause.lexicon import Token
from lawvm.finland.johtolause.sentinels import SKIP_CATS

# Pure structural punctuation / glue that legitimately sits between produced
# nodes and is never expected to carry an op by itself.  A run consisting only
# of these (plus SKIP_CATS sentinels) is acceptable, not a silent drop.
_GLUE_CATS: frozenset[str] = frozenset(
    {
        "COMMA",
        "CONJ",  # ja / sekä / tai
        "SEP",
        "DASH",  # range dash between covered endpoints
        "VERB",  # the verb token itself is glue; its targets carry the witness
        "END",
    }
)

# Cats that are acceptable as trailing residue (sentinels) — END sentinel
# ``seuraavasti:`` and citation/provenance/reinstatement spans.
_ACCEPTABLE_TRAILING: frozenset[str] = SKIP_CATS


@dataclass(frozen=True)
class UnconsumedSpan:
    """A maximal run of non-sentinel tokens covered by no produced node.

    Attributes:
        start_token: first token index of the run (in the filtered stream).
        end_token:   one-past-last token index of the run.
        char_start:  source-text char offset (inclusive), or -1 if unknown.
        char_end:    source-text char offset (exclusive), or -1 if unknown.
        source_text: the verbatim unparsed fragment (self-evidencing).
        token_cats:  the category sequence of the run (shape signature).
        trailing:    True if the run is at the end of the clause (truncation
                     tail) rather than wedged between two covered spans.
    """

    start_token: int
    end_token: int
    char_start: int
    char_end: int
    source_text: str
    token_cats: tuple[str, ...]
    trailing: bool


def _iter_witness_spans(node: object) -> Iterable[tuple[int, int]]:
    """Yield (start, end) token spans for a node and any witness-bearing children."""
    witness = getattr(node, "witness", None)
    if witness is not None:
        span = getattr(witness, "source_span", None)
        if span is not None:
            start, end = span
            if start is not None and end is not None and end > start:
                yield (int(start), int(end))

    # Recurse into container nodes that hold witness-bearing children.
    for attr in ("nodes", "sub_refs", "children", "members", "targets"):
        child_seq = getattr(node, attr, None)
        if child_seq:
            for child in child_seq:
                yield from _iter_witness_spans(child)


def covered_token_indices(clause: object) -> set[int]:
    """Compute the set of token indices covered by any produced node's witness."""
    covered: set[int] = set()
    verb_groups = getattr(clause, "verb_groups", ()) or ()
    for vg in verb_groups:
        for node in getattr(vg, "nodes", ()) or ():
            for start, end in _iter_witness_spans(node):
                covered.update(range(start, end))
    for meta in getattr(clause, "meta_clauses", ()) or ():
        for start, end in _iter_witness_spans(meta):
            covered.update(range(start, end))
    for ta in getattr(clause, "text_amend_clauses", ()) or ():
        for start, end in _iter_witness_spans(ta):
            covered.update(range(start, end))
    return covered


def _char_span(tokens: list[Token], start: int, end: int) -> tuple[int, int]:
    starts = [tokens[i].char_start for i in range(start, end) if tokens[i].char_start >= 0]
    ends = [tokens[i].char_end for i in range(start, end) if tokens[i].char_end >= 0]
    if not starts or not ends:
        return (-1, -1)
    return (min(starts), max(ends))


def audit_token_coverage(
    tokens: list[Token],
    clause: object,
    *,
    source_text: Optional[str] = None,
) -> list[UnconsumedSpan]:
    """Report maximal runs of non-sentinel, non-glue tokens covered by no node.

    A run is reported only if it contains at least one CONTENT token — a token
    that is neither a SKIP_CATS sentinel nor pure glue (punctuation/conjunction/
    verb/dash).  Runs of glue-or-sentinel only are legitimate connective tissue
    and are not silent drops.
    """
    if source_text is None:
        source_text = getattr(clause, "source_text", "") or ""

    covered = covered_token_indices(clause)
    n = len(tokens)

    spans: list[UnconsumedSpan] = []
    i = 0
    while i < n:
        if i in covered or tokens[i].cat in _ACCEPTABLE_TRAILING:
            i += 1
            continue
        # Start of an uncovered run.
        run_start = i
        while i < n and i not in covered and tokens[i].cat not in _ACCEPTABLE_TRAILING:
            i += 1
        run_end = i

        run_cats = tuple(tokens[j].cat for j in range(run_start, run_end))
        has_content = any(c not in _GLUE_CATS for c in run_cats)
        if not has_content:
            continue

        cs, ce = _char_span(tokens, run_start, run_end)
        frag = source_text[cs:ce] if (cs >= 0 and ce >= 0 and source_text) else ""
        trailing = run_end >= n or all(
            (k in covered) is False for k in range(run_end, n)
        )
        spans.append(
            UnconsumedSpan(
                start_token=run_start,
                end_token=run_end,
                char_start=cs,
                char_end=ce,
                source_text=frag,
                token_cats=run_cats,
                trailing=bool(trailing),
            )
        )

    return spans


def audit_johtolause(text: str) -> list[UnconsumedSpan]:
    """Convenience: lex + parse ``text`` and audit token coverage."""
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause import surface_parse as _sp

    tokens = tokenize(text)
    clause = _sp.parse(tokens)
    return audit_token_coverage(tokens, clause, source_text=text)


# Signal tiers for a classified uncovered span, strongest first.
#   verb_no_op        — the run contains a structural VERB (a whole operation
#                       the parser recognized as a verb but produced nothing).
#                       Highest signal: e.g. "korvataan taulukko".
#   unmatched_section — the run names a "N §" whose label is NOT a target of any
#                       produced op.  A section mentioned but not compiled.
#   preamble_only     — the run is reinstatement/citation/provenance glue around
#                       a produced op (op label IS present elsewhere).  Low
#                       signal: usually a witness-span fidelity gap, not a drop.
_TIER_VERB_NO_OP = "verb_no_op"
_TIER_UNMATCHED_SECTION = "unmatched_section"
_TIER_PREAMBLE_ONLY = "preamble_only"
_TIER_OTHER = "other"


@dataclass(frozen=True)
class ClassifiedSpan:
    """An ``UnconsumedSpan`` tagged with a triage tier and any named labels.

    ``position`` is one of: ``leading_preamble`` (before any produced op — the
    enactment formula, ignore), ``interior`` (between two produced ops — a real
    mid-stream drop), ``trailing`` (after the last produced op — a truncation
    tail), or ``no_ops`` (nothing was produced at all).
    """

    tier: str
    labels: tuple[str, ...]
    span: UnconsumedSpan
    position: str = "unknown"


def classify_uncovered_spans(text: str) -> list[ClassifiedSpan]:
    """Run the coverage audit and tier each uncovered span for fast triage.

    Cross-checks against produced ``parsed_ops`` target labels.  This is a
    TRIAGE instrument, not an oracle: ``verb_no_op`` and ``unmatched_section``
    are the high-signal tiers worth inspecting; ``preamble_only`` is mostly
    witness-span fidelity noise around a correctly produced op.
    """
    import re

    from lawvm.finland.johtolause.api import parse_clause
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause import surface_parse as _sp

    tokens = tokenize(text)
    clause = _sp.parse(tokens)
    spans = audit_token_coverage(tokens, clause, source_text=text)

    parsed = parse_clause(text, statute_id="AUDIT")
    op_labels = {
        re.sub(r"\s+", "", (op.number or "")).lower()
        for op in (parsed.parsed_ops or [])
        if op.number
    }

    covered = covered_token_indices(clause)
    first_cov = min(covered) if covered else None
    last_cov = max(covered) if covered else None

    label_re = re.compile(r"(\d+\s*[a-zA-Z]?)\s*§")
    out: list[ClassifiedSpan] = []
    for span in spans:
        # Position relative to produced-node coverage.  A span entirely BEFORE
        # the first covered token is the enactment preamble ("Suomen Senaatti
        # on, ... esittelyssä", "Me NIKOLAI Toinen, ...") — the parser correctly
        # ignores it; it is NOT a silent operation drop.  Only INTERIOR (between
        # covered spans) and TRAILING (after the last covered token, when some
        # op was produced) spans indicate a clause the parser dropped mid-stream.
        if first_cov is None:
            position = "no_ops"  # nothing produced at all — separate failure mode
        elif span.end_token <= first_cov:
            position = "leading_preamble"
        elif last_cov is not None and span.start_token > last_cov:
            position = "trailing"
        else:
            position = "interior"

        labels = tuple(
            re.sub(r"\s+", "", m.group(1)).lower() for m in label_re.finditer(span.source_text)
        )
        unmatched = tuple(lb for lb in labels if lb and lb not in op_labels)
        has_verb = "VERB" in span.token_cats

        # A span is a REAL drop only when it carries evidence the parser missed
        # something: a section LABEL that no produced op targets (unmatched), or
        # a structural VERB token that the span names but no op covers AND the
        # span has no labels at all (a verbed clause that produced nothing).
        # A span whose labels are ALL already produced is a witness-fidelity gap
        # (the op exists, its witness span is just narrow) — NOT a drop.  Tiering
        # those as drops caused a ~50% false-positive rate in the verb_no_op
        # tier (e.g. 1978/588, 1977/1002: every flagged label was in ops).
        if position == "leading_preamble":
            tier = _TIER_PREAMBLE_ONLY
        elif position == "no_ops":
            tier = _TIER_OTHER
        elif unmatched:
            # Genuine: a named section the parser produced no op for.
            tier = _TIER_VERB_NO_OP if has_verb else _TIER_UNMATCHED_SECTION
        elif has_verb and not labels:
            # A verbed clause naming no section label and producing nothing —
            # e.g. "korvataan taulukko" (a whole operation the parser dropped).
            tier = _TIER_VERB_NO_OP
        else:
            # Labels all matched (witness-fidelity gap) or pure glue.
            tier = _TIER_PREAMBLE_ONLY
        out.append(
            ClassifiedSpan(tier=tier, labels=labels, span=span, position=position)
        )
    return out
