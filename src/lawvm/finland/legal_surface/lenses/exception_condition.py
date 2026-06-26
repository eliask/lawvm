"""H6 exception/condition surface lens (Pro r5 Phase 8 — nodes only).

Adapts the H6 closed-list exception/condition recognizer into a
:class:`lawvm.core.legal_surface_lens.SurfaceLens`. It emits one
``exception_condition_cue`` surface node per recognised cue and NO edges. The
condition and exception cue families SHARE this one node kind (discriminated by
``cue_kind`` ∈ {"EXCEPTION", "CONDITION"} in the payload). The recognizer has no
residual class — a matched cue whose scope can't be bounded is still a valid cue
with ``scope_hint=None`` — so this lens emits no residual seeds. Cross-frame
edges/lints are DEFERRED per Pro r5 Phase 8.

SAFETY BOUNDARY (mirrors the recognizer — the EASIEST family to overclaim):
SURFACE FACTS ONLY. A node records that a closed-list cue word appears, its
verbatim marker, and a COARSE ``scope_hint`` pointer. It NEVER resolves which
rule is excepted/overridden, never classifies the cue legally, never links to a
target provision. Status is the structural ``NODE_STATUSES`` value ``"asserted"``.

PHASE 7 SUBSTRATE MIGRATION (Pro r5 §D4): this lens consumes the
source-preserving ``unit.token_tape`` view rather than regex-scanning
``raw_text``. The closed EXCEPTION/CONDITION marker phrases are matched against
token SEQUENCES (normalized), reproducing the recognizer's word-boundary and
clause-initial-ish semantics on tokens. ``required_views=("token_tape",)``.

The migration is held to EXACT behavior identity with the pre-migration regex
recognizer (``recognize_exception_condition_cues``): same cue set, same
``cue_kind`` / ``marker_text`` / ``scope_hint`` payloads, same ``source_ref``
spans. ``tests/test_fi_tokentape.py`` asserts this on synthetic fixtures and on
real statutes against the regex recognizer as the oracle.

Span alignment: the token spans are character offsets into ``unit.raw_text``;
each ``SourceSpanRef`` is built directly from those offsets. The cue-marker span
anchors the node; the coarse scope-hint span travels in the payload.
"""
from __future__ import annotations

import hashlib

from lawvm.core.legal_surface_graph import SourceSpanRef
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
    SurfaceLensResult,
    SurfaceNodeSeed,
)
from lawvm.core.legal_surface_tokens import Token, TokenTape
from lawvm.finland.legal_surface.clause_segment import (
    bound_scope_hint as _shared_bound_scope_hint,
)
from lawvm.finland.legal_surface.clause_segment import (
    is_clause_initial_ish as _shared_is_clause_initial_ish,
)
from lawvm.finland.legal_surface.tokenize import build_token_tape

_LENS_ID = "fi.exception_condition.v0"
_RULE_ID = "fi.surface.exception_condition.v1"

# Closed marker lists — MUST mirror
# lawvm.finland.references.exception_condition (the behavior oracle). Each phrase
# is pre-split into its word tokens for token-sequence matching.
_EXCEPTION_MARKERS: tuple[str, ...] = (
    "poiketen siitä mitä",
    "ei kuitenkaan",
    "lukuun ottamatta",
    "sen estämättä",
    "jollei",
    "paitsi",
    "ellei",
)
_CONDITION_MARKERS: tuple[str, ...] = (
    "sillä edellytyksellä että",
    "edellyttäen että",
    "siltä osin kuin",
    "mikäli",
    "jos",
    "kun",
)

#: Cues needing the stricter clause-initial-ish guard (common short words).
_CLAUSE_INITIAL_CUES: frozenset[str] = frozenset({"jos", "kun"})

# The clause-initial / scope-bounding rules come from the SHARED authority
# (imported above) via the delegating _is_clause_initial_ish / _bound_scope_hint
# helpers below. This lens keeps no private copy of the clause-boundary logic.
_MAX_SCOPE_HINT = 200


def _phrase_words(markers: tuple[str, ...]) -> list[list[str]]:
    """Pre-split markers into casefolded word lists, longest (word count) first.

    Ordered to mirror the recognizer's longest-first alternation so a longer
    phrase is preferred at a given position.
    """
    split = [[w.casefold() for w in m.split(" ")] for m in markers]
    split.sort(key=lambda ws: sum(len(w) for w in ws), reverse=True)
    return split


_EXCEPTION_PHRASES = _phrase_words(_EXCEPTION_MARKERS)
_CONDITION_PHRASES = _phrase_words(_CONDITION_MARKERS)


def _span_ref(unit: SourceSurfaceUnit, start: int, end: int) -> SourceSpanRef:
    surface = unit.raw_text[start:end]
    return SourceSpanRef(
        source_unit_id=unit.source_unit_id,
        source_hash=unit.source_hash,
        work_id=unit.work_id,
        address=unit.address,
        char_start=start,
        char_end=end,
        text_hash=hashlib.sha256(surface.encode("utf-8")).hexdigest(),
    )


def _word_boundary_before(tokens: tuple[Token, ...], idx: int) -> bool:
    """Is there a regex word-boundary immediately before token ``idx``?

    The recognizer's word-char class is ``[\\wäöåÄÖÅ]`` (letters, DIGITS, ``_``).
    A boundary fails only when the directly-preceding character (no whitespace
    gap) is a word char — i.e. the previous token is a ``word``/``number``/
    ``colon_suffix`` (which ends in letters) abutting this one, or ``_`` punct.
    """
    if idx == 0:
        return True
    prev = tokens[idx - 1]
    cur = tokens[idx]
    if prev.char_end != cur.char_start:
        return True  # something (whitespace/dash/punct) separates them
    # directly adjacent: boundary fails iff prev's last char is a word char
    return not _ends_in_word_char(prev)


def _word_boundary_after(tokens: tuple[Token, ...], idx: int) -> bool:
    """Is there a regex word-boundary immediately after token ``idx``?"""
    if idx + 1 >= len(tokens):
        return True
    cur = tokens[idx]
    nxt = tokens[idx + 1]
    if cur.char_end != nxt.char_start:
        return True
    return not _starts_with_word_char(nxt)


def _ends_in_word_char(tok: Token) -> bool:
    if not tok.text:
        return False
    last = tok.text[-1]
    return _is_word_char(last)


def _starts_with_word_char(tok: Token) -> bool:
    if not tok.text:
        return False
    return _is_word_char(tok.text[0])


def _is_word_char(ch: str) -> bool:
    # mirror re's \w (Unicode word chars incl. digits and underscore) plus the
    # äöåÄÖÅ already covered by \w under Unicode, but keep explicit for clarity.
    return ch.isalnum() or ch == "_"


def _try_match_phrase(
    tokens: tuple[Token, ...], start_idx: int, phrase: list[str]
) -> int | None:
    """Try to match ``phrase`` (word list) starting at token ``start_idx``.

    Returns the index of the LAST matched token (inclusive), or None. Words may
    be separated by whitespace tokens only (mirroring the recognizer's ``\\s+``
    between phrase words). Enforces word boundaries before the first and after
    the last matched token.
    """
    if not phrase or not _word_boundary_before(tokens, start_idx):
        return None
    idx = start_idx
    last_idx = start_idx
    for w_i, word in enumerate(phrase):
        if idx >= len(tokens):
            return None
        tok = tokens[idx]
        if tok.normalized != word:
            return None
        last_idx = idx
        idx += 1
        if w_i < len(phrase) - 1:
            # require at least one whitespace token between phrase words
            if idx >= len(tokens) or tokens[idx].category != "whitespace":
                return None
            idx += 1  # consume the whitespace
            # collapse any further whitespace tokens (defensive; tokenizer emits
            # one whitespace run per gap, so this is normally a no-op)
            while idx < len(tokens) and tokens[idx].category == "whitespace":
                idx += 1
    if not _word_boundary_after(tokens, last_idx):
        return None
    return last_idx


def _is_clause_initial_ish(raw_text: str, start: int) -> bool:
    """Delegate to the SHARED clause-boundary authority (char offsets)."""
    return _shared_is_clause_initial_ish(raw_text, start)


def _bound_scope_hint(raw_text: str, after: int) -> tuple[int, int] | None:
    """Delegate to the SHARED clause-boundary authority (char offsets)."""
    return _shared_bound_scope_hint(raw_text, after, max_len=_MAX_SCOPE_HINT)


def _scan_phrases(
    tape: TokenTape,
    raw_text: str,
    phrases: list[list[str]],
    cue_kind: str,
) -> list[tuple[int, int, str, int | None, int | None]]:
    """Token-scan one marker family.

    Returns tuples ``(marker_start, marker_end, marker_text, scope_start,
    scope_end)`` — mirroring one ``_scan`` pass of the recognizer. The marker
    span is the char range of the matched token sequence; ``marker_text`` is the
    matched tokens joined by single spaces (matching the recognizer's
    ``re.sub(r"\\s+", " ", group)`` over the verbatim surface).
    """
    tokens = tape.tokens
    out: list[tuple[int, int, str, int | None, int | None]] = []
    phrases_by_first_word: dict[str, list[list[str]]] = {}
    for phrase in phrases:
        if phrase:
            phrases_by_first_word.setdefault(phrase[0], []).append(phrase)
    for i in range(len(tokens)):
        # try longest-first; first phrase that matches at i wins (mirrors the
        # regex alternation which is anchored at each position longest-first).
        for phrase in phrases_by_first_word.get(tokens[i].normalized, ()):
            last_idx = _try_match_phrase(tokens, i, phrase)
            if last_idx is None:
                continue
            m_start = tokens[i].char_start
            m_end = tokens[last_idx].char_end
            # verbatim surface, internal whitespace collapsed to single space
            verbatim = raw_text[m_start:m_end]
            marker_text = " ".join(verbatim.split())
            if (
                marker_text.lower() in _CLAUSE_INITIAL_CUES
                and not _is_clause_initial_ish(raw_text, m_start)
            ):
                break  # mid-clause jos/kun: skip (precision over recall)
            bounds = _bound_scope_hint(raw_text, m_end)
            if bounds is None:
                out.append((m_start, m_end, marker_text, None, None))
            else:
                out.append((m_start, m_end, marker_text, bounds[0], bounds[1]))
            break  # do not match a shorter phrase at the same start
    return out


class ExceptionConditionLens:
    """SurfaceLens over the H6 exception/condition cues, consuming token_tape."""

    lens_id: str = _LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = "v0"
    produces_node_kinds: tuple[str, ...] = ("exception_condition_cue",)
    produces_edge_kinds: tuple[str, ...] = ()
    required_views: tuple[str, ...] = ("token_tape",)

    def analyze(
        self,
        bundle: SourceSurfaceBundle,
        *,
        context: SurfaceAnalysisContext,
    ) -> SurfaceLensResult:
        node_seeds: list[SurfaceNodeSeed] = []
        units_scanned = 0
        for unit in bundle.units:
            units_scanned += 1
            tape = unit.token_tape
            # The substrate populates token_tape; tolerate an un-tokenized unit
            # by building the tape on demand (fail-loud only if raw_text absent).
            if not isinstance(tape, TokenTape):
                tape = build_token_tape(unit.source_unit_id, unit.raw_text)

            matches = _scan_phrases(
                tape, unit.raw_text, _EXCEPTION_PHRASES, "EXCEPTION"
            )
            cond = _scan_phrases(
                tape, unit.raw_text, _CONDITION_PHRASES, "CONDITION"
            )
            # tag kinds, then sort by marker start (mirror recognizer's sort)
            tagged: list[tuple[int, int, str, int | None, int | None, str]] = []
            for m in matches:
                tagged.append((*m, "EXCEPTION"))
            for m in cond:
                tagged.append((*m, "CONDITION"))
            tagged.sort(key=lambda t: t[0])

            for m_start, m_end, marker_text, sc_start, sc_end, kind in tagged:
                ref = _span_ref(unit, m_start, m_end)
                scope_payload = (
                    [sc_start, sc_end]
                    if sc_start is not None and sc_end is not None
                    else None
                )
                node_seeds.append(
                    SurfaceNodeSeed(
                        node_kind="exception_condition_cue",
                        source_ref=ref,
                        local_discriminator=(
                            f"{kind}|{marker_text}|{m_start}|{len(node_seeds)}"
                        ),
                        rule_id=_RULE_ID,
                        node_status="asserted",
                        payload={
                            "cue_kind": kind,
                            "marker_text": marker_text,
                            "scope_hint": scope_payload,
                        },
                        authority_role="surface_fact",
                    )
                )

        return SurfaceLensResult(
            lens_id=self.lens_id,
            node_seeds=tuple(node_seeds),
            edge_seeds=(),
            residuals=(),
            diagnostics=(),
            coverage={
                "units_scanned": units_scanned,
                "exception_condition_cues": len(node_seeds),
            },
        )
