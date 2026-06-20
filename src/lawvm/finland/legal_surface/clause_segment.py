"""Deterministic Finnish clause/sentence segmentation over a source unit.

This is the **clause-segmentation substrate** of the Legal Surface Algebra: a
deterministic, source-anchored split of a unit's text into ``sentences`` and
finer ``clauses``, exposed as a :class:`~lawvm.core.legal_surface_tokens.ClauseIndex`
view on the unit (mirroring how ``token_tape`` / ``morph_overlay`` attach). It is
the shared substrate later attachment passes query ("which clause owns this
span?") instead of the magic 120-char colocation window that over-generates.

Placement (the universal/local split, Pro r5 §D2/§D4): the carrier types
(``ClauseSpan`` / ``SentenceSpan`` / ``ClauseIndex``) and the span→clause query
are jurisdiction-NEUTRAL and live in ``core``. The segmentation RULES below —
which characters/cues bound a Finnish clause, which dotted tokens are NOT
sentence ends — are Finnish and live HERE.

This phase makes NO attachment decisions and emits NO composition edges. It only
produces the queryable index + the ONE shared clause-boundary authority that
``lawvm.finland.references.exception_condition`` now consumes (rule-of-three: the
clause splitter had grown a second in-tree copy inside that recognizer).

Determinism + bound: a single left-to-right pass over the unit's lossless
:class:`TokenTape`; no regex, no backtracking. Cost is linear in the token
count.

Segmentation rules
==================
SENTENCE boundaries are ``.`` / ``!`` / ``?`` / newline, EXCEPT a ``.`` that is
NOT a real sentence end:
  * a dotted NUMBER token (``1.1.2027`` dates, ``12.5`` decimals — the tokenizer
    keeps these as a single ``number`` token, so an internal ``.`` is never even
    a separate token);
  * a section-number dot (``5 §`` / ``§ 5`` neighbourhoods, and ``N.`` directly
    before a ``§``);
  * an ordinal dot: a ``.`` directly after a bare integer (``1.``, ``2.``) — a
    list ordinal / ordinal number, not a sentence end;
  * a closed-list abbreviation dot (``esim.``, ``ns.``, ``mm.`` …).

A ``!`` / ``?`` always ends a sentence (they do not appear in the dotted-number /
abbreviation contexts). Newline always ends a sentence.

SUB-CLAUSE boundaries WITHIN a sentence are:
  * ``,`` and ``;`` (the classic clause boundaries — also what the
    exception/condition recognizer's ``_CLAUSE_BOUNDARY_CHARS`` used);
  * a subordinating cue token at a clause-initial-ish position: ``jos``, ``kun``,
    ``jollei``, ``ellei``, ``mikäli``, plus the multi-word cues ``edellyttäen
    että``, ``siltä osin kuin``;
  * a coordinating ``ja`` / ``tai`` / ``sekä`` ONLY when it bounds a clause —
    here, conservatively, only when it is immediately preceded by a comma
    boundary (``…, ja …``), the canonical clause-coordinating use; a bare
    ``X ja Y`` noun coordination does NOT split (precision over recall).

The cue cases open a NEW clause starting AT the cue. Punctuation boundaries
close the current clause AFTER the punctuation. Leading/trailing whitespace is
trimmed off every emitted span; an all-whitespace span is dropped.
"""
from __future__ import annotations

import hashlib
from bisect import bisect_left

from lawvm.core.legal_surface_tokens import (
    ClauseIndex,
    ClauseSpan,
    SegmentationGraph,
    SentenceSpan,
    StructuralSegment,
    Token,
    TokenTape,
)
from lawvm.finland.legal_surface.tokenize import build_token_tape

# ── shared clause-boundary authority (lifted from the H6 recognizer) ──────────

#: Characters that count as a clause boundary, for sub-clause splitting AND for
#: the exception/condition recognizer's scope-bounding + clause-initial guard.
#: This is the SINGLE authority; the recognizer imports it from here.
CLAUSE_BOUNDARY_CHARS: str = ",;.\n"

#: Additional clause-initial-ish openers a cue may directly follow.
CLAUSE_INITIAL_OPENERS: str = ":(["


def is_clause_initial_ish(text: str, start: int) -> bool:
    """Is the offset ``start`` clause-initial-ish in ``text``?

    True iff everything before ``start`` back to the most recent non-whitespace
    char is a clause boundary (:data:`CLAUSE_BOUNDARY_CHARS`), an opening paren
    (:data:`CLAUSE_INITIAL_OPENERS`), or start-of-text. SURFACE check only.

    This is the lifted authority the H6 exception/condition recognizer consumes
    (it previously kept a private copy — the rule-of-three trigger). Behaviour is
    byte-identical to that copy.
    """
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0:
        return True
    return text[i] in CLAUSE_BOUNDARY_CHARS or text[i] in CLAUSE_INITIAL_OPENERS


def bound_scope_hint(
    text: str, after: int, *, max_len: int
) -> tuple[int, int] | None:
    """Bound a coarse scope-hint span starting after offset ``after``.

    SURFACE ONLY: the run of text from the first non-whitespace char after
    ``after`` up to (not including) the next clause boundary
    (:data:`CLAUSE_BOUNDARY_CHARS`), bounded by ``max_len`` characters. Returns
    ``(start, end)`` or ``None`` when nothing but whitespace/boundary follows.

    Lifted authority for the H6 recognizer (which previously kept a private copy
    with its own ``_MAX_SCOPE_HINT``); the recognizer passes its own max_len so
    behaviour stays byte-identical.
    """
    start = after
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text):
        return None
    limit = min(len(text), start + max_len)
    end = start
    while end < limit and text[end] not in CLAUSE_BOUNDARY_CHARS:
        end += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end <= start:
        return None
    return (start, end)


# ── Finnish sentence-split guards ─────────────────────────────────────────────

#: Closed-list abbreviations whose trailing ``.`` does NOT end a sentence. The
#: key is the casefolded abbreviation body WITHOUT its dot. Audited tuple — a new
#: abbreviation is a deliberate edit here, never a heuristic.
_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "esim",  # esimerkiksi
        "ns",  # niin sanottu
        "mm",  # muun muassa
        "ml",  # mukaan luettuna / lukien
        "nk",  # niin kutsuttu
        "ko",  # kyseessä oleva
        "ym",  # ynnä muuta
        "yms",  # ynnä muuta sellaista
        "jne",  # ja niin edelleen
        "vrt",  # vertaa
        "ks",  # katso
        "tms",  # tai muuta sellaista
        "ent",  # entinen
        "nro",  # numero
        "art",  # artikla
    }
)

#: Subordinating cue word tokens (single-word) that open a sub-clause when
#: clause-initial-ish. ``jos`` / ``kun`` are common-and-ambiguous so they keep
#: the clause-initial guard; the others are unambiguous enough but we apply the
#: same guard uniformly (a mid-sentence ``mikäli`` is itself clause-initial-ish
#: in practice, so this does not lose them).
_SUBORDINATOR_WORDS: frozenset[str] = frozenset(
    {"jos", "kun", "jollei", "ellei", "mikäli"}
)

#: Multi-word subordinating cues (lists of casefolded word tokens). Matched as a
#: token sequence (whitespace tokens between words are skipped).
_SUBORDINATOR_PHRASES: tuple[tuple[str, ...], ...] = (
    ("edellyttäen", "että"),
    ("siltä", "osin", "kuin"),
)

#: Coordinating conjunctions that bound a clause ONLY in the ``…, ja …`` shape
#: (immediately preceded by a comma boundary). Precision over recall.
_COORDINATORS: frozenset[str] = frozenset({"ja", "tai", "sekä"})

#: Sentence-ending punctuation that is unconditional (never dotted-number /
#: abbreviation context).
_HARD_SENTENCE_END: frozenset[str] = frozenset({"!", "?"})


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prev_nonspace_token(tokens: tuple[Token, ...], idx: int) -> Token | None:
    j = idx - 1
    while j >= 0 and tokens[j].category == "whitespace":
        j -= 1
    return tokens[j] if j >= 0 else None


def _next_nonspace_token(tokens: tuple[Token, ...], idx: int) -> Token | None:
    j = idx + 1
    n = len(tokens)
    while j < n and tokens[j].category == "whitespace":
        j += 1
    return tokens[j] if j < n else None


def _dot_is_sentence_end(tokens: tuple[Token, ...], idx: int) -> bool:
    """Does the ``.`` punct token at ``idx`` end a sentence?

    The internal-dot cases of a decimal/date (``12.5``, ``1.1.2027``) never reach
    here: the tokenizer folds an internal ``.`` (between digits) into a single
    ``number`` token, so a standalone ``.`` punct token is only ever a true
    period, an ordinal dot, an abbreviation dot, or a section-number dot.

    The number guard is DELIBERATELY NARROW so a genuine sentence end after a
    number (the canonical commencement sentence ``... voimaan 1.1.2027.``) is NOT
    swallowed: the trailing ``.`` of ``1.1.2027`` ends the sentence, because the
    number token ``1.1.2027`` already CONTAINS dots — only a BARE integer
    (``1``, ``2``, ``5``) directly before the dot is treated as an ordinal /
    section-number dot.
    """
    prev = _prev_nonspace_token(tokens, idx)
    nxt = _next_nonspace_token(tokens, idx)
    dot = tokens[idx]

    # Section-number dot: a dot immediately followed by a section mark ("5. §").
    if nxt is not None and nxt.category == "section_mark":
        return False
    if (
        prev is not None
        and prev.category == "number"
        and prev.char_end == dot.char_start  # number directly abuts the dot
        and "." not in prev.text  # BARE integer only (not a dotted date/decimal)
    ):
        # ordinal dot ("1.", "2.") or a section-number — neither ends a sentence.
        # A dotted number ("1.1.2027.") is left to end the sentence normally.
        return False

    # Abbreviation dot: the previous adjacent token is a word in the closed list,
    # with NO space between the word and the dot (e.g. "esim.").
    if (
        prev is not None
        and prev.category == "word"
        and prev.char_end == tokens[idx].char_start
        and prev.normalized in _ABBREVIATIONS
    ):
        return False

    return True


def sentence_terminator_between(
    tokens: tuple[Token, ...], lo: int, hi: int
) -> bool:
    """Does a SENTENCE boundary fall strictly between char offsets ``lo`` and ``hi``?

    Uses the SAME sentence-split authority as :func:`build_clause_index`: a
    ``!``/``?`` punct token, a ``.`` punct token that :func:`_dot_is_sentence_end`
    (so dotted dates/decimals, ordinal/section-number dots, and closed-list
    abbreviation dots do NOT count), or a whitespace token containing a newline.
    The boundary must lie within the half-open interval ``[lo, hi)`` — ``lo`` is
    the actor's exclusive end offset, so a terminator AT ``lo`` is the char
    immediately after the actor (its sentence ending right there) and DOES
    separate it from a later modal. A terminator at/after ``hi`` does not.

    This is the shared guard the surface frame recognizers use to refuse fusing an
    actor in one sentence with a modal/sanction predicate in the next: it scans
    the lossless tape's tokens, so no raw-text reconstruction is needed.
    """
    if hi <= lo:
        return False
    for idx, tok in enumerate(tokens):
        # A punct/word terminator counts at its own char_start; a newline counts
        # at the position of the newline char inside the whitespace run. The
        # half-open window [lo, hi) admits a terminator abutting the actor end.
        if tok.char_end <= lo:
            continue
        if tok.char_start >= hi:
            break
        if tok.category == "whitespace":
            if "\n" in tok.text:
                nl = tok.char_start + tok.text.index("\n")
                if lo <= nl < hi:
                    return True
            continue
        if tok.category != "punct":
            continue
        ch = tok.text
        if ch in _HARD_SENTENCE_END:
            if lo <= tok.char_start < hi:
                return True
        elif ch == "." and _dot_is_sentence_end(tokens, idx):
            if lo <= tok.char_start < hi:
                return True
    return False


def _match_phrase(
    tokens: tuple[Token, ...], start_idx: int, phrase: tuple[str, ...]
) -> int | None:
    """Match ``phrase`` (casefolded word list) at ``start_idx``; return last idx."""
    idx = start_idx
    last = start_idx
    for w_i, word in enumerate(phrase):
        if idx >= len(tokens) or tokens[idx].normalized != word:
            return None
        last = idx
        idx += 1
        if w_i < len(phrase) - 1:
            # require at least one whitespace token between phrase words
            if idx >= len(tokens) or tokens[idx].category != "whitespace":
                return None
            while idx < len(tokens) and tokens[idx].category == "whitespace":
                idx += 1
    return last


def _trim(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Trim whitespace off ``[start, end)``; None if empty after trim."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end <= start:
        return None
    return (start, end)


def build_clause_index(
    source_unit_id: str,
    raw_text: str,
    *,
    token_tape: TokenTape | None = None,
) -> ClauseIndex:
    """Build a deterministic Finnish clause/sentence index over ``raw_text``.

    A single left-to-right token pass: sentence boundaries first (the guarded
    ``.`` / ``!`` / ``?`` / newline rule), then sub-clause boundaries WITHIN each
    sentence (``,`` / ``;`` + subordinating/coordinating cues). Returns a
    :class:`ClauseIndex` whose spans are document-ordered, non-overlapping,
    whitespace-trimmed, and clause⊆sentence.

    ``token_tape`` may be supplied (the substrate populates it) to avoid
    re-tokenizing; otherwise a tape is built on demand.
    """
    text = raw_text
    tape = token_tape if isinstance(token_tape, TokenTape) else build_token_tape(
        source_unit_id, raw_text
    )
    tokens = tape.tokens
    token_starts = tuple(token.char_start for token in tokens)

    # ── pass 1: sentence boundaries ──────────────────────────────────────────
    # A sentence boundary index is the char offset just AFTER the boundary char.
    sentence_cuts: list[int] = []
    for idx, tok in enumerate(tokens):
        if tok.category == "whitespace":
            if "\n" in tok.text:
                # newline ends a sentence at the FIRST newline in the run
                nl = tok.text.index("\n")
                sentence_cuts.append(tok.char_start + nl + 1)
            continue
        if tok.category != "punct":
            continue
        ch = tok.text
        if ch in _HARD_SENTENCE_END:
            sentence_cuts.append(tok.char_end)
        elif ch == ".":
            if _dot_is_sentence_end(tokens, idx):
                sentence_cuts.append(tok.char_end)

    sentence_spans = _spans_from_cuts(text, sentence_cuts)
    sentences = tuple(
        SentenceSpan(char_start=s, char_end=e) for (s, e) in sentence_spans
    )

    # ── pass 2: sub-clause boundaries within each sentence ───────────────────
    clauses: list[ClauseSpan] = []
    for sent_index, (s_start, s_end) in enumerate(sentence_spans):
        clauses.extend(
            _segment_sentence_clauses(
                text,
                tokens,
                token_starts,
                s_start,
                s_end,
                sent_index,
            )
        )

    return ClauseIndex(
        source_unit_id=source_unit_id,
        text_hash=_sha256_text(raw_text),
        sentences=sentences,
        clauses=tuple(clauses),
    )


def _spans_from_cuts(text: str, cuts: list[int]) -> list[tuple[int, int]]:
    """Turn sorted boundary offsets into trimmed, non-empty spans covering text."""
    bounds = sorted(set(cut for cut in cuts if 0 < cut <= len(text)))
    spans: list[tuple[int, int]] = []
    prev = 0
    for cut in bounds:
        trimmed = _trim(text, prev, cut)
        if trimmed is not None:
            spans.append(trimmed)
        prev = cut
    if prev < len(text):
        trimmed = _trim(text, prev, len(text))
        if trimmed is not None:
            spans.append(trimmed)
    return spans


def _segment_sentence_clauses(
    text: str,
    tokens: tuple[Token, ...],
    token_starts: tuple[int, ...],
    s_start: int,
    s_end: int,
    sent_index: int,
) -> list[ClauseSpan]:
    """Split one sentence span into sub-clauses. Returns trimmed ClauseSpans."""
    # Walk the tokens that fall inside [s_start, s_end).
    i = bisect_left(token_starts, s_start)
    clause_open = s_start  # char offset where the current clause opens
    open_kind = "sentence"
    out: list[ClauseSpan] = []

    def _flush(close_at: int, kind: str) -> None:
        trimmed = _trim(text, clause_open, close_at)
        if trimmed is not None:
            out.append(
                ClauseSpan(
                    char_start=trimmed[0],
                    char_end=trimmed[1],
                    sentence_index=sent_index,
                    clause_kind=kind,
                )
            )

    n = len(tokens)
    while i < n and tokens[i].char_start < s_end:
        tok = tokens[i]
        # punctuation clause boundary: , or ; (close AFTER the punct)
        if tok.category == "punct" and tok.text in (",", ";"):
            _flush(tok.char_end, open_kind)
            clause_open = tok.char_end
            open_kind = "comma" if tok.text == "," else "semicolon"
            i += 1
            continue

        # coordinating ja/tai/sekä in the "…, ja …" shape: split BEFORE the cue
        if (
            tok.category == "word"
            and tok.normalized in _COORDINATORS
            and _preceded_by_comma(text, tok.char_start, clause_open)
        ):
            _flush(tok.char_start, open_kind)
            clause_open = tok.char_start
            open_kind = f"coordinator:{tok.normalized}"
            i += 1
            continue

        # subordinating cue: split BEFORE the cue when clause-initial-ish, but
        # NOT if the cue IS already the clause opener (avoid a zero-width split).
        if tok.category == "word":
            phrase_last = _matched_subordinator(tokens, i)
            if (
                phrase_last is not None
                and tok.char_start > clause_open
                and _trim(text, clause_open, tok.char_start) is not None
                and is_clause_initial_ish(text, tok.char_start)
            ):
                _flush(tok.char_start, open_kind)
                clause_open = tok.char_start
                open_kind = f"subordinator:{tok.normalized}"
                i = phrase_last + 1
                continue

        i += 1

    _flush(s_end, open_kind)
    return out


def _matched_subordinator(tokens: tuple[Token, ...], idx: int) -> int | None:
    """If a subordinator (single word or phrase) starts at ``idx``, last idx."""
    tok = tokens[idx]
    # multi-word first (longest)
    for phrase in _SUBORDINATOR_PHRASES:
        if tok.normalized == phrase[0]:
            last = _match_phrase(tokens, idx, phrase)
            if last is not None:
                return last
    if tok.normalized in _SUBORDINATOR_WORDS:
        return idx
    return None


def _preceded_by_comma(text: str, cue_start: int, clause_open: int) -> bool:
    """Is the cue at ``cue_start`` immediately preceded (mod whitespace) by ','?

    Only counts a comma that lies AT or after the current clause-open offset, so
    a coordinator at the very start of a clause (opened by that same comma) still
    counts as the canonical ``…, ja …`` split point.
    """
    i = cue_start - 1
    while i >= clause_open - 1 and i >= 0 and text[i].isspace():
        i -= 1
    return i >= 0 and text[i] == ","


# ─────────────────────────────────────────────────────────────────────────────
# SegmentationGraph — additive STRUCTURAL segmentation (one level above clauses)
# ─────────────────────────────────────────────────────────────────────────────
#
# Coordinate space + the central honest limitation
# -------------------------------------------------
# The body text segmented here is the unit's ``raw_text`` — what
# ``bundle.decode_body_text`` produces: each statute ``<p>`` element's content,
# newline-joined. CRUCIAL CONSEQUENCE for this layer: that extraction keeps ONLY
# ``<p>`` text and DROPS the sibling ``<num>`` markers (``1)``, ``a)``, ``5 §``)
# and the ``<intro>``/``<paragraph>`` nesting. So in THIS coordinate space the
# enumeration markers DO NOT EXIST. We therefore detect list structure by the
# SURFACE shape the markers leave behind (a colon-terminated chapeau governing
# the lines that follow it, each terminated by ``;`` / ``;  sekä`` / ``.``), and
# we record — as the segment ``role`` — that the enumeration index itself is not
# in the tape. This is the prompt's "say so honestly" case: the marker is
# honestly absent, not silently guessed.
#
# The unit of segmentation is the PHYSICAL LINE (one ``<p>`` == one
# newline-delimited line). A single deterministic left-to-right pass splits the
# tape on newlines into line content-spans and the whitespace gaps between them;
# classifies each non-empty line into a segment kind by its line shape; then
# links list items to their governing chapeau.
#
# TOTAL TOKEN OWNERSHIP: the gaps (leading/trailing/inter-line whitespace,
# including the newlines themselves) are emitted as EXPLICIT ``residual`` segments
# (reason ``"benign_whitespace"``), so the segments partition ``[0, len(text))``
# exactly — the no-silent-drop invariant the carrier enforces.

#: Trailing phrases (casefolded, trimmed of the colon) that mark the chapeau of a
#: QUOTED-AMENDMENT block rather than (or in addition to) an enumerated list —
#: the lines following are quoted statutory text being inserted/substituted.
_QUOTED_AMENDMENT_LEADINS: tuple[str, ...] = (
    "kuuluu seuraavasti",
    "kuuluu näin",
    "kuuluvat seuraavasti",
    "seuraavasti",
    "aiempi sanamuoto kuuluu",
    "sanamuoto kuuluu",
)

#: Casefolded definitional cues: a chapeau containing one of these governs a
#: DEFINITION list (its items are definitional entries). Surface-only tag.
_DEFINITION_CHAPEAU_CUES: tuple[str, ...] = (
    "tarkoitetaan",
)


def _physical_lines(text: str) -> list[tuple[int, int]]:
    """Split ``text`` into physical-line content spans (newline-delimited).

    Returns the TRIMMED content span of each non-empty line, in document order.
    Pure surface split on ``\\n``; the whitespace between/around lines is owned
    separately as residual by the segmentation builder. An all-whitespace line
    contributes no span (its whitespace falls into the surrounding residual).
    """
    spans: list[tuple[int, int]] = []
    n = len(text)
    line_start = 0
    i = 0
    while i <= n:
        if i == n or text[i] == "\n":
            trimmed = _trim(text, line_start, i)
            if trimmed is not None:
                spans.append(trimmed)
            line_start = i + 1
        i += 1
    return spans


def _last_nonspace_char(text: str, start: int, end: int) -> str:
    j = end - 1
    while j >= start and text[j].isspace():
        j -= 1
    return text[j] if j >= start else ""


def _looks_like_heading(text: str, start: int, end: int) -> bool:
    """A heading line: short title-ish content with NO sentence-terminal punct.

    Headings in the decoded body are the statute number/title lines and
    subheadings (väliotsikko): short, no trailing ``.``/``;``/``:``, and not a
    full sentence. Conservative: only a SHORT line (<= 60 chars trimmed) whose
    last non-space char is a letter/digit (no terminal punctuation) and which
    contains no sentence-internal ``. `` qualifies. Precision over recall — a
    misclassified heading would only mislabel; ownership is unaffected.
    """
    body = text[start:end]
    if len(body) > 60:
        return False
    last = _last_nonspace_char(text, start, end)
    if not last or not last.isalnum():
        return False
    return ". " not in body


def _ends_with_colon(text: str, start: int, end: int) -> bool:
    return _last_nonspace_char(text, start, end) == ":"


def _is_quoted_amendment_leadin(text: str, start: int, end: int) -> bool:
    """Does this colon-terminated line lead in a quoted-amendment block?"""
    low = text[start:end].casefold().rstrip()
    if not low.endswith(":"):
        return False
    stem = low[:-1].rstrip()
    return any(stem.endswith(cue) for cue in _QUOTED_AMENDMENT_LEADINS)


def _is_definition_chapeau(text: str, start: int, end: int) -> bool:
    low = text[start:end].casefold()
    return any(cue in low for cue in _DEFINITION_CHAPEAU_CUES)


def _is_list_item_shape(text: str, start: int, end: int) -> bool:
    """Does the trimmed line END like an enumerated item (``;`` or terminal ``.``)?

    Surface-only: the line's last non-space char is ``;`` (canonical item
    terminator, possibly followed by a trailing ``sekä``/``ja``/``tai`` we look
    through), or ``.`` (last item). Used ONLY when a governing chapeau is open;
    outside a list context a ``.``-terminated line is ordinary prose.
    """
    low = text[start:end].casefold().rstrip()
    if low.endswith(";"):
        return True
    for tail in (" sekä", " ja", " tai"):
        if low.endswith(tail):
            head = low[: -len(tail)].rstrip()
            if head.endswith(";"):
                return True
    return low.endswith(".")


def build_segmentation_graph(
    source_unit_id: str,
    raw_text: str,
) -> SegmentationGraph:
    """Build the additive STRUCTURAL :class:`SegmentationGraph` over ``raw_text``.

    Deterministic single pass over the physical lines of the decoded body. Every
    char of ``raw_text`` is owned by exactly one segment: a typed structural
    segment (heading / chapeau / list_item / quoted_amendment_block / prose) for
    line content, or an explicit ``residual`` (reason ``"benign_whitespace"``)
    for the whitespace gaps. List items carry a ``parent_index`` link to their
    governing chapeau (list inheritance). Surface-only; makes NO attachment or
    composition decisions and is NOT yet projected into the surface graph.

    See the module section header for the coordinate-space limitation: the
    enumeration markers (``1)`` / ``a)`` / ``5 §``) are NOT in this coordinate
    space (``decode_body_text`` keeps only ``<p>`` content), so list membership
    is detected by chapeau government + item line-shape, and the absent marker is
    recorded on the segment ``role`` rather than guessed.
    """
    text = raw_text
    lines = _physical_lines(text)

    segments: list[StructuralSegment] = []
    cursor = 0

    # Open-chapeau state for list inheritance: the index (in ``segments``) of the
    # most recent chapeau, and whether it is a quoted-amendment / definition one.
    open_chapeau_index: int | None = None
    open_chapeau_quoted = False
    open_chapeau_definition = False

    def _emit_residual_gap(up_to: int) -> None:
        nonlocal cursor
        if up_to > cursor:
            segments.append(
                StructuralSegment(
                    char_start=cursor,
                    char_end=up_to,
                    kind="residual",
                    residual_reason="benign_whitespace",
                )
            )
            cursor = up_to

    for li, (l_start, l_end) in enumerate(lines):
        _emit_residual_gap(l_start)  # own the whitespace gap before this line

        kind = "prose"
        role = ""
        parent_index: int | None = None
        is_first_two = li < 2  # statute number + title lead the decoded body

        if _ends_with_colon(text, l_start, l_end):
            # a chapeau: opens a following list (and/or a quoted-amendment block)
            kind = "chapeau"
            if _is_quoted_amendment_leadin(text, l_start, l_end):
                open_chapeau_quoted = True
                role = "quoted_amendment_chapeau"
            else:
                open_chapeau_quoted = False
                role = "colon_chapeau"
            open_chapeau_definition = _is_definition_chapeau(text, l_start, l_end)
            if open_chapeau_definition and not open_chapeau_quoted:
                role = "definition_list"
            open_chapeau_index = len(segments)
        elif open_chapeau_index is not None and open_chapeau_quoted:
            # inside a quoted-amendment block: the quoted statutory text (a line
            # that itself ends in ':' was handled above and re-opens a chapeau).
            kind = "quoted_amendment_block"
            parent_index = open_chapeau_index
        elif open_chapeau_index is not None and _is_list_item_shape(
            text, l_start, l_end
        ):
            # an enumerated item under the open chapeau (list inheritance).
            kind = "list_item"
            # enumeration marker (1)/a)/–) is NOT in this coordinate space; record
            # that honestly on the role rather than fabricating an index.
            role = (
                "definition_entry_marker_not_in_tape"
                if open_chapeau_definition
                else "enum_marker_not_in_tape"
            )
            parent_index = open_chapeau_index
            if _last_nonspace_char(text, l_start, l_end) == ".":
                # a '.'-terminated item closes the list
                open_chapeau_index = None
                open_chapeau_quoted = False
                open_chapeau_definition = False
        else:
            # not under (or no longer under) a chapeau: heading or prose. The
            # chapeau context, if any, is closed by this non-item line.
            open_chapeau_index = None
            open_chapeau_quoted = False
            open_chapeau_definition = False
            if _looks_like_heading(text, l_start, l_end):
                kind = "heading"
                role = "title_line" if is_first_two else "subheading"
            else:
                kind = "prose"

        segments.append(
            StructuralSegment(
                char_start=l_start,
                char_end=l_end,
                kind=kind,
                role=role,
                parent_index=parent_index,
            )
        )
        cursor = l_end

    _emit_residual_gap(len(text))  # trailing whitespace after the last line

    return SegmentationGraph(
        source_unit_id=source_unit_id,
        text_hash=_sha256_text(raw_text),
        text_len=len(text),
        segments=tuple(segments),
    )
