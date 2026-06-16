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

# Structural-noun token categories: the lexicon tags the unit word of an
# addressable amendment target (``§`` / ``luku`` / ``momentti`` / ``kohta`` /
# ``osa`` / ``liite`` / ``nimike`` / ``otsikko`` / ``alakohta``) with its own
# cat.  A dropped operative unit is a NUM (or NUM-tail) followed by one of these
# — NOT just ``N §``.  The old drop predicate regexed only ``(\d+)\s*§`` against
# the span text, so a dropped ``N luku`` / ``N momentti`` / ``luvun nimike`` was
# structurally invisible.  Keying off these cats makes the predicate
# unit-agnostic without per-unit regex special-casing.
_UNIT_CATS: dict[str, str] = {
    "PYKALA": "§",
    "LUKU": "luku",
    "MOMENTTI": "momentti",
    "KOHTA": "kohta",
    "ALAKOHTA": "alakohta",
    "OSA": "osa",
    "LIITE": "liite",
    "NIMIKE": "nimike",
    "OTSIKKO": "otsikko",
}

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
    """Convenience: lex + annotate + parse ``text`` and audit token coverage.

    Mirrors the production token path (``apply_annotations_with_jolloin_pairs``
    -> ``parse``) so the audited token indices align with the SurfaceClause's
    witness indices.  A raw-token re-parse would misalign them.
    """
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs
    from lawvm.finland.johtolause import surface_parse as _sp

    raw_tokens = tokenize(text)
    tokens, jolloin_pairs = apply_annotations_with_jolloin_pairs(raw_tokens)
    clause = _sp.parse(tokens, jolloin_renumber_pairs=jolloin_pairs or None)
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


_KIND_TO_UNIT: dict[str, str] = {"P": "§", "L": "luku", "M": "momentti"}


def op_label_keys(op: object) -> set[str]:
    """Unit-qualified + bare label keys for a produced op.

    ``op.number`` is the bare ordinal ("6") regardless of unit; the addressed
    unit lives in ``op.kind`` (P=§, L=luku, M=momentti).  A span naming ``6
    luku`` must NOT be masked as "matched" by a produced ``6 §`` op, so the
    match key is unit-qualified ("6luku" vs "6§").  We emit BOTH the
    unit-qualified key and the bare number: the unit-qualified key gives the
    precise discrimination the unit-agnostic span predicate needs, while the
    bare number preserves backward-compatible matching for callers/units we do
    not map.  Measurement-only — no parse behaviour rides on this.
    """
    import re as _re

    num = _re.sub(r"\s+", "", (getattr(op, "number", "") or "")).lower()
    if not num:
        return set()
    keys = {num}
    unit = _KIND_TO_UNIT.get(getattr(op, "kind", "") or "")
    if unit:
        keys.add(num + unit)
    return keys


def _extract_unit_labels(
    span: UnconsumedSpan,
    tokens: list[Token],
    text: str,
) -> list[str]:
    """Unit-qualified labels named in a span, read from its TOKENS (not regex).

    Scans the span's token slice for a NUM (optional letter suffix) immediately
    followed by a structural-unit token (``§`` / ``luku`` / ``momentti`` / …).
    The label is the normalized number + the unit word ("6§", "6luku",
    "23nimike"), so every addressable unit kind is visible, not just ``N §``.

    Number-tail guard (preserved from the §-only predicate): when a produced
    op's witness covers only the leading digit(s) of a section number, the
    trailing digit(s) leak into this uncovered span ("36 §" head-covered -> a
    phantom "6 §" at the span start).  A NUM whose first char is immediately
    preceded by a digit in the RAW source is such a tail and is rejected.
    """
    import re as _re

    labels: list[str] = []
    s, e = span.start_token, span.end_token
    for i in range(s, e):
        tok = tokens[i]
        if tok.cat != "NUM":
            continue
        # The unit token may sit one slot ahead (NUM then PYKALA/LUKU/...), with
        # an optional letter-suffix NOM/WORD ("69 a §") between them.
        j = i + 1
        if j < e and tokens[j].cat in ("NOM", "WORD") and len(tokens[j].text) <= 2 and tokens[j].text.isalpha():
            suffix = tokens[j].text.lower()
            j += 1
        else:
            suffix = ""
        if j >= e or tokens[j].cat not in _UNIT_CATS:
            continue
        unit = _UNIT_CATS[tokens[j].cat]
        # Number-tail guard: reject a NUM whose first digit is glued to a
        # preceding digit in the raw text (a leaked partial-witness tail).
        cs = tok.char_start
        if 0 < cs <= len(text) and text[cs - 1].isdigit():
            continue
        num = _re.sub(r"\s+", "", tok.text).lower()
        labels.append(num + suffix + unit)
    return labels


def classify_uncovered_spans(text: str) -> list[ClassifiedSpan]:
    """Run the coverage audit and tier each uncovered span for fast triage.

    Cross-checks against produced ``parsed_ops`` target labels.  This is a
    TRIAGE instrument, not an oracle: ``verb_no_op`` and ``unmatched_section``
    are the high-signal tiers worth inspecting; ``preamble_only`` is mostly
    witness-span fidelity noise around a correctly produced op.

    ALIGNMENT (instrument-correctness fix): the production silent-drop path in
    ``api.py`` classifies the FILTERED token stream (``raw_tokens`` after
    ``apply_annotations_with_jolloin_pairs``) against the SurfaceClause that
    same filtered stream produced.  The previous version of this wrapper instead
    re-parsed the RAW token tape (``tokenize`` -> ``surface_parse.parse``), so
    its witness token-indices were offset from the filtered indices the audit
    walks -> real drops were mislabelled ``no_ops``/``other`` and silently
    excluded.  We now mirror ``api.py`` exactly: tokenize -> annotate -> parse
    the filtered stream -> classify against THAT, so the instrument and the
    production diagnostic agree.
    """
    from lawvm.finland.johtolause.api import parse_clause
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs
    from lawvm.finland.johtolause import surface_parse as _sp

    raw_tokens = tokenize(text)
    tokens, jolloin_pairs = apply_annotations_with_jolloin_pairs(raw_tokens)
    clause = _sp.parse(tokens, jolloin_renumber_pairs=jolloin_pairs or None)

    parsed = parse_clause(text, statute_id="AUDIT")
    op_labels: set[str] = set()
    for op in parsed.parsed_ops or []:
        op_labels |= op_label_keys(op)
    return classify_spans_from_parsed(text, tokens, clause, op_labels)


def classify_spans_from_parsed(
    text: str,
    tokens: list[Token],
    clause: object,
    op_labels: set[str],
) -> list[ClassifiedSpan]:
    """Core classifier over ALREADY-parsed inputs (no re-parse, hot-path safe).

    ``classify_uncovered_spans`` is the convenience wrapper that lexes/parses
    ``text`` and derives ``op_labels``; callers that already hold the token
    stream, the parsed ``SurfaceClause``, and the produced op-number set (e.g.
    ``parse_clause`` itself, emitting a silent-drop diagnostic) pass them here to
    avoid recursion and a redundant parse.
    """
    spans = audit_token_coverage(tokens, clause, source_text=text)

    covered = covered_token_indices(clause)
    first_cov = min(covered) if covered else None
    last_cov = max(covered) if covered else None

    out: list[ClassifiedSpan] = []
    for span in spans:
        # Position relative to produced-node coverage.  A span entirely BEFORE
        # the first covered token is the enactment preamble ("Suomen Senaatti
        # on, ... esittelyssä", "Me NIKOLAI Toinen, ...") — the parser correctly
        # ignores it; it is NOT a silent operation drop.  Only INTERIOR (between
        # covered spans), TRAILING (after the last covered token, when some op
        # was produced), and NO_OPS (nothing produced at all — a whole-clause
        # drop) spans indicate a clause the parser dropped operative content.
        if first_cov is None:
            position = "no_ops"  # nothing produced at all — a whole-clause drop
        elif span.end_token <= first_cov:
            position = "leading_preamble"
        elif last_cov is not None and span.start_token > last_cov:
            position = "trailing"
        else:
            position = "interior"

        # Extract UNIT-QUALIFIED labels named in the span from its TOKENS, so a
        # dropped ``N luku`` / ``N momentti`` / ``luvun nimike`` is visible — not
        # just ``N §`` (the old §-only regex predicate's blind spot).  The
        # number-tail guard ("36 §" head-covered -> phantom "6 §") is preserved
        # inside the extractor.
        labels = tuple(_extract_unit_labels(span, tokens, text))
        unmatched = tuple(lb for lb in labels if lb and lb not in op_labels)
        has_verb = "VERB" in span.token_cats

        # A span is a REAL drop only when it carries evidence the parser missed
        # something: a unit LABEL that no produced op targets (unmatched), or a
        # structural VERB token that the span names but no op covers AND the span
        # has no labels at all (a verbed clause that produced nothing).  A span
        # whose labels are ALL already produced is a witness-fidelity gap (the op
        # exists, its witness span is just narrow) — NOT a drop.  Tiering those
        # as drops caused a ~50% false-positive rate in the verb_no_op tier (e.g.
        # 1978/588, 1977/1002: every flagged label was in ops).
        #
        # NO_OPS handling (instrument-correctness fix): a no_ops span is NOT
        # automatically safe.  Previously every no_ops span was bucketed as
        # _TIER_OTHER and excluded, hiding whole-clause drops (a clause that
        # produced zero ops but names operative units).  When the clause produced
        # ops yet a span shows no_ops, that is an ALIGNMENT failure, not a clean
        # parse, and must not be silently dropped either.  We therefore tier
        # no_ops spans by the same label/verb evidence as interior/trailing.
        if position == "leading_preamble":
            tier = _TIER_PREAMBLE_ONLY
        elif unmatched:
            # Genuine: a named unit the parser produced no op for.
            tier = _TIER_VERB_NO_OP if has_verb else _TIER_UNMATCHED_SECTION
        elif has_verb and not labels:
            # A verbed clause naming no unit label and producing nothing —
            # e.g. "korvataan taulukko" (a whole operation the parser dropped).
            tier = _TIER_VERB_NO_OP
        elif position == "no_ops" and labels:
            # Whole-clause drop: the clause produced no ops at all, yet the span
            # names operative unit labels.  None can be "matched" (op_labels is
            # empty), so this is a genuine drop, not a witness-fidelity gap.
            tier = _TIER_UNMATCHED_SECTION
        else:
            # Labels all matched (witness-fidelity gap) or pure glue.
            tier = _TIER_PREAMBLE_ONLY
        out.append(
            ClassifiedSpan(tier=tier, labels=labels, span=span, position=position)
        )
    return out
