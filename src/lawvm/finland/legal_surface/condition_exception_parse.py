"""Condition / exception construction parse — the conditional-adjunct island.

The FIFTH and final core construction-grammar island of the Finnish
SourceSyntaxGraph north star, after the citation-sentence pilot
(:mod:`lawvm.finland.legal_surface.sentence_parse`), the definition-entry pilot
(:mod:`lawvm.finland.legal_surface.definition_parse`), the temporal /
applicability island (:mod:`lawvm.finland.legal_surface.temporal_parse`), and
the modal / deontic-core island (:mod:`lawvm.finland.legal_surface.modal_parse`):
the **condition / exception family**. A conditional/exceptive clause is the
formulaic Finnish adjunct that QUALIFIES a provision — it fixes WHEN a norm
applies (a condition) or carves out a case where it does NOT (an exception):

  * **conditions** — ``jos X, …`` / ``kun X, …`` / ``jollei …`` / ``ellei …`` /
    ``mikäli …`` / ``edellyttäen että …`` / ``sillä edellytyksellä että …`` /
    ``siltä osin kuin …``;
  * **exceptions** — ``ei kuitenkaan …`` / ``sen estämättä mitä … säädetään`` /
    ``paitsi …`` / ``lukuun ottamatta …`` / ``poiketen siitä mitä … säädetään``.

(``jollei`` / ``ellei`` are negative conditionals the production lens lists under
EXCEPTION — "unless"; we keep the production cue→kind mapping verbatim so the
projection is comparable by construction.)

Position in the stack
=====================
Same discipline as the four prior islands, one family over: a sentence-frame
construction with TOTAL TOKEN OWNERSHIP (every char of the sentence is a typed
construction span — the cue, the conditioned/excepted clause span, the matrix
(governed) span, or an EXPLICIT residual; the invariant is "no silent drop", NOT
"no residue"). It is purely ADDITIVE and surface-only: it makes NO legal
conclusion (it does NOT assert a rule is overridden / limited / unenforceable),
authorizes NO replay, and is NOT wired into the production exception/condition
lens.

The NORTH-STAR value over the production H6 lens
================================================
The production ``exception_condition`` lens (the H6 surface cue lens) records the
cue + a COARSE ``scope_hint`` — the run of text up to the next clause boundary.
That is a proximity pointer, not a parse: it does not say WHAT the clause scopes.
The construction parse adds the missing structure:

  * the CONDITIONED / EXCEPTED clause subtree (the adjunct clause the cue opens),
    bounded by the SHARED clause-segmentation authority (the same clause boundary
    rules the H6 lens consumes), NOT a magic char window; and
  * the ATTACHMENT — which deontic core (modal island) / matrix clause the
    qualifier scopes. Attachment is fixed by linking to the modal cores
    :func:`lawvm.finland.legal_surface.modal_parse.parse_modal_sentence` finds in
    the SAME sentence. Exactly ONE modal core in the matrix → ``resolved``; more
    than one (genuine ambiguity which one the qualifier scopes) → ``ambiguous``;
    none (the matrix carries no recognized deontic core) → ``candidate`` (the
    qualifier exists but its target is not yet a typed core). We NEVER silently
    pick a target — tag-don't-guess.

WEAK ORACLE CAVEAT (read before trusting miss == 0)
===================================================
Like the modal island, the differential oracle here — the production H6
``exception_condition`` lens — is WEAK, but in the OPPOSITE direction: it
OVER-generates. It fires one cue-fact per closed-list cue occurrence with a
proximity ``scope_hint``; it does not require the cue to head a real adjunct
clause, and its ``jos`` / ``kun`` guard is precision-tuned but still coarse. So:

  * ``miss == 0`` means little — the oracle emits a cue-fact for every cue the
    construction parse also keys on (we MIRROR the production closed cue lists),
    so the projection rarely lacks one.
  * The REAL gates are (a) total-token-ownership / no-silent-drop
    (``LAWVM_PARSE_TOTALITY``); (b) a CHEAP-SIGNAL proxy — sentences carrying a
    condition/exception cue surface that the construction parse fails to turn into
    a parsed clause (candidate miss); (c) an ATTACHMENT-QUALITY spot-check — does
    the construction attach the qualifier to the right modal core, and does it
    correctly mark ``ambiguous`` rather than over-attach the way the proximity
    window does. All three are reported NEUTRALLY.

The construction
================
A condition/exception parse over a sentence span carries:

  * zero or more **qualifiers** — each a closed-list ``cue`` (the cue surface,
    casefolded to its closed-list token), a ``kind`` (closed list: ``condition`` /
    ``exception``), the **qualified clause span** (the adjunct clause the cue
    opens, bounded by the shared clause authority), and an **attachment** (the
    index of the modal core it scopes + an attachment ``status`` —
    ``resolved`` / ``ambiguous`` / ``candidate``);
  * the **modal cores** of the sentence (the attachment targets, from the modal
    island), carried for reference;
  * an explicit **residual** span list — every char NOT owned by a qualifier's
    cue or qualified-clause span, typed by reason. The no-silent-drop invariant
    holds because the residual is EXPLICIT.

:func:`assert_total_ownership` is the checkable postcondition (the union of the
qualifier cue spans, qualified-clause spans, and residual spans partitions the
sentence char range exactly).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lawvm.finland.legal_surface.clause_segment import (
    CLAUSE_BOUNDARY_CHARS,
    is_clause_initial_ish,
)
from lawvm.finland.legal_surface.modal_parse import ModalCore, parse_modal_sentence

# Reuse the PRODUCTION closed cue lists verbatim; do NOT reimplement cue
# recognition. Mirroring the production tuples keeps the census in parity by
# construction (the projection keys on the same cue surfaces the oracle emits).
from lawvm.finland.references.exception_condition import (
    _CLAUSE_INITIAL_CUES,
    _CONDITION_MARKERS,
    _EXCEPTION_MARKERS,
)

# ---------------------------------------------------------------------------
# Parser-lane provenance — mirrors modal_parse / temporal_parse.
# ---------------------------------------------------------------------------
#: The condition/exception grammar owned the frame (in-scope, no-silent-drop).
CONDEXC_LANE_CONSTRUCTION_OWNED = "condexc_construction_owned"
#: The frame declined: the span carried a condition/exception cue the family
#: discriminator keyed on, but NO recognizable qualifier clause parsed. Handed
#: back as typed residue, never a guessed parse.
CONDEXC_LANE_DECLINED = "condexc_construction_declined"

# ---------------------------------------------------------------------------
# Closed-list qualifier kinds. Names the SURFACE shape (NOT the legal force — no
# "override"/"limitation"/"proviso" conclusion). Mirrors the production
# ``CueKind`` (EXCEPTION / CONDITION), lower-cased for the family vocabulary.
# ---------------------------------------------------------------------------
KIND_CONDITION = "condition"
KIND_EXCEPTION = "exception"

# ---------------------------------------------------------------------------
# Attachment status — the construction-grammar value add over the proximity
# ``scope_hint``. Which deontic core the qualifier scopes, and how confidently.
# ---------------------------------------------------------------------------
#: Exactly one modal core in the sentence → the qualifier unambiguously scopes it.
ATTACH_RESOLVED = "resolved"
#: More than one modal core → genuinely ambiguous which the qualifier scopes; we
#: record the nearest as a candidate target but flag the ambiguity (never guess).
ATTACH_AMBIGUOUS = "ambiguous"
#: No modal core in the sentence → the qualifier exists but its target is not yet
#: a typed deontic core (it scopes a non-modal matrix clause or an out-of-island
#: provision). Tagged, not invented.
ATTACH_CANDIDATE = "candidate"

#: Map each closed-list cue (casefolded) to its kind. Mirrors the production
#: split: the EXCEPTION list (incl. the negative conditionals ``jollei`` /
#: ``ellei``) → exception; the CONDITION list → condition.
_CUE_KIND: dict[str, str] = {}
for _m in _EXCEPTION_MARKERS:
    _CUE_KIND[_m.casefold()] = KIND_EXCEPTION
for _m in _CONDITION_MARKERS:
    _CUE_KIND[_m.casefold()] = KIND_CONDITION

#: All closed-list cues, longest-first so multi-word cues
#: (``sillä edellytyksellä että``, ``poiketen siitä mitä``) beat their prefixes.
_CUE_TOKENS_LONGEST_FIRST: tuple[str, ...] = tuple(
    sorted(set(_CUE_KIND), key=len, reverse=True)
)

#: Finnish word-char class for the cue word-boundary guards (mirror the
#: production ``_WC`` negative-lookaround discipline).
_WC = r"[\wäöåÄÖÅ]"


def _build_cue_re() -> re.Pattern[str]:
    """Compile a longest-first, word-bounded alternation over the closed cues.

    Inter-word whitespace inside a multi-word cue matches ``\\s+`` (mirrors the
    production ``_build_re``), so ``poiketen   siitä mitä`` still matches.
    """
    alternation = "|".join(
        r"\s+".join(re.escape(word) for word in cue.split(" "))
        for cue in _CUE_TOKENS_LONGEST_FIRST
    )
    return re.compile(
        r"(?<!" + _WC + r")(?:" + alternation + r")(?!" + _WC + r")",
        re.IGNORECASE,
    )


_CUE_RE = _build_cue_re()


@dataclass(frozen=True)
class Residual:
    """An explicit unowned span of the sentence (no-silent-drop typed residue)."""

    char_start: int
    char_end: int
    reason: str


@dataclass(frozen=True)
class Qualifier:
    """One condition/exception qualifier the sentence carries.

    Attributes:
        kind:           Closed-list surface shape (``condition`` / ``exception``).
        cue:            The cue surface, casefolded to its closed-list token (e.g.
                        ``jos`` / ``ei kuitenkaan`` / ``sen estämättä`` /
                        ``edellyttäen että``).
        cue_start:      Char offset (sentence-local) where the cue begins.
        cue_end:        One-past the cue.
        clause_start:   Char offset where the qualified (conditioned/excepted)
                        adjunct clause begins, or ``None`` when no scope bounded.
        clause_end:     One-past the qualified clause, or ``None``.
        attachment_status: ``resolved`` / ``ambiguous`` / ``candidate`` — how
                        confidently the qualifier attaches to a deontic core.
        attached_core_index: Index (into the parse's ``cores`` tuple) of the modal
                        core this qualifier scopes, or ``None`` (candidate — no
                        core to attach to). For ``ambiguous`` this is the NEAREST
                        candidate, recorded but explicitly flagged ambiguous.
    """

    kind: str
    cue: str
    cue_start: int
    cue_end: int
    clause_start: int | None
    clause_end: int | None
    attachment_status: str
    attached_core_index: int | None


@dataclass(frozen=True)
class ConditionExceptionParse:
    """A condition/exception sentence construction parse (the lite IR).

    Attributes:
        seg_start / seg_end: Sentence char range (sentence-local; the parse runs
                             on ``text`` so ``seg_start == 0``).
        text:                The exact sentence text.
        kind:                ``"condexc"`` when >=1 qualifier parsed; ``"declined"``
                             when a cue was present but no qualifier parsed.
        qualifiers:          The recognized qualifiers, in source order.
        cores:               The sentence's modal cores (the attachment targets),
                             from the modal island, for reference.
        residuals:           Explicit unowned spans (the no-silent-drop residue).
        parser_lane:         Which lane produced this frame (closed set above).
    """

    seg_start: int
    seg_end: int
    text: str
    kind: str
    qualifiers: tuple[Qualifier, ...]
    cores: tuple[ModalCore, ...]
    residuals: tuple[Residual, ...] = field(default_factory=tuple)
    parser_lane: str = CONDEXC_LANE_CONSTRUCTION_OWNED


def _has_condexc_cue(text: str) -> bool:
    return _CUE_RE.search(text) is not None


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _fill_residuals(n: int, owned: list[tuple[int, int]], reason: str) -> list[Residual]:
    residuals: list[Residual] = []
    cursor = 0
    for s, e in _merge_intervals(owned):
        if s > cursor:
            residuals.append(Residual(cursor, s, reason))
        cursor = max(cursor, e)
    if cursor < n:
        residuals.append(Residual(cursor, n, reason))
    return residuals


def _bound_qualified_clause(text: str, after: int) -> tuple[int | None, int | None]:
    """Bound the qualified (conditioned/excepted) adjunct clause after the cue.

    The clause is the run from the first non-whitespace char after the cue up to
    (not including) the next clause boundary (:data:`CLAUSE_BOUNDARY_CHARS`), using
    the SAME shared clause-segmentation authority the production H6 lens consumes —
    NOT a magic char window. Returns ``(None, None)`` when nothing but
    whitespace/boundary follows (a scope is NEVER guessed).
    """
    start = after
    n = len(text)
    while start < n and text[start].isspace():
        start += 1
    if start >= n:
        return None, None
    end = start
    while end < n and text[end] not in CLAUSE_BOUNDARY_CHARS:
        end += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end <= start:
        return None, None
    return start, end


def _attach(
    cores: tuple[ModalCore, ...], cue_start: int
) -> tuple[str, int | None]:
    """Resolve the qualifier's attachment to a deontic core (tag-don't-guess).

    Attachment rule (surface, conservative):
      * no modal core in the sentence → ``candidate`` (no typed target yet);
      * exactly one modal core         → ``resolved`` (unambiguous);
      * more than one modal core        → ``ambiguous`` — record the NEAREST core
        (by cue distance) as the candidate target but flag the ambiguity. We never
        silently commit to one of several plausible targets.
    """
    if not cores:
        return ATTACH_CANDIDATE, None
    if len(cores) == 1:
        return ATTACH_RESOLVED, 0
    nearest = min(
        range(len(cores)),
        key=lambda i: abs(cores[i].cue_start - cue_start),
    )
    return ATTACH_AMBIGUOUS, nearest


def parse_condition_exception_sentence(text: str) -> ConditionExceptionParse:
    """Parse one sentence span into condition/exception qualifier constructions.

    ``text`` is the EXACT sentence span, in its own local coordinate system.
    Deterministic: first parse the sentence's modal cores (the attachment targets)
    with the modal island; then scan for closed-list condition/exception cues
    (longest-first, word-bounded, mirroring the production matcher) and for each
    cue emit ONE qualifier — classified into a kind by the production cue→kind
    split, owning the cue surface span and the qualified adjunct-clause span
    (bounded by the shared clause authority), and attached to a modal core with an
    explicit ``resolved`` / ``ambiguous`` / ``candidate`` status. Every other char
    is typed explicit residual.

    The ``jos`` / ``kun`` cues fire ONLY when clause-initial-ish (mirrors the
    production precision-over-recall guard). Declines (typed residue, never a
    guessed parse) when NO qualifier parses (the caller's family discriminator
    guarantees a cue for in-scope spans, so a decline here is the cue-present /
    no-clause case — e.g. a ``jos`` mid-clause that fails the initial-ish guard).
    """
    n = len(text)
    # Attachment targets: the deontic cores of THIS sentence (modal island).
    cores = parse_modal_sentence(text).cores

    qualifiers: list[Qualifier] = []
    owned: list[tuple[int, int]] = []
    for m in _CUE_RE.finditer(text):
        norm = re.sub(r"\s+", " ", m.group(0)).casefold()
        kind = _CUE_KIND.get(norm)
        if kind is None:
            continue
        if norm in _CLAUSE_INITIAL_CUES and not is_clause_initial_ish(text, m.start()):
            # mid-clause jos/kun: precision over recall (mirror production), skip.
            continue
        cue_start, cue_end = m.start(), m.end()
        c_start, c_end = _bound_qualified_clause(text, cue_end)
        status, core_idx = _attach(cores, cue_start)
        qualifiers.append(
            Qualifier(
                kind=kind,
                cue=norm,
                cue_start=cue_start,
                cue_end=cue_end,
                clause_start=c_start,
                clause_end=c_end,
                attachment_status=status,
                attached_core_index=core_idx,
            )
        )
        owned.append((cue_start, cue_end))
        if c_start is not None and c_end is not None:
            owned.append((c_start, c_end))

    if not qualifiers:
        return ConditionExceptionParse(
            seg_start=0,
            seg_end=n,
            text=text,
            kind="declined",
            qualifiers=(),
            cores=cores,
            residuals=(Residual(0, n, "no_condexc_qualifier"),),
            parser_lane=CONDEXC_LANE_DECLINED,
        )

    residuals = _fill_residuals(n, owned, "benign_uninterpreted_prose")
    return ConditionExceptionParse(
        seg_start=0,
        seg_end=n,
        text=text,
        kind="condexc",
        qualifiers=tuple(qualifiers),
        cores=cores,
        residuals=tuple(residuals),
        parser_lane=CONDEXC_LANE_CONSTRUCTION_OWNED,
    )


def assert_total_ownership(cp: ConditionExceptionParse) -> None:
    """Checkable postcondition: the frame's spans partition ``[seg_start, seg_end)``.

    The union of qualifier cue spans, qualified-clause spans, and the explicit
    residual spans must cover every char of the sentence with NO gap and NO silent
    drop. (The modal cores carried for attachment are NOT counted — they are
    reference targets, and their own ownership is the modal island's invariant.)
    Raises ``AssertionError`` on violation.
    """
    n = cp.seg_end - cp.seg_start
    covered = [False] * n
    spans: list[tuple[int, int]] = []
    for q in cp.qualifiers:
        spans.append((q.cue_start, q.cue_end))
        if q.clause_start is not None and q.clause_end is not None:
            spans.append((q.clause_start, q.clause_end))
    spans.extend((r.char_start, r.char_end) for r in cp.residuals)
    for s, e in spans:
        for i in range(max(0, s), min(n, e)):
            covered[i] = True
    missing = [i for i, c in enumerate(covered) if not c]
    if missing:
        raise AssertionError(
            f"total-ownership violation: {len(missing)} unowned chars in sentence "
            f"(first gap at {missing[0]}); SILENT DROP. text={cp.text!r}"
        )


# ---------------------------------------------------------------------------
# Projection: ConditionExceptionParse -> [production exception_condition key]
# ---------------------------------------------------------------------------


def condexc_key(kind: str, cue: str) -> str:
    """Canonical census key for one qualifier.

    Keyed on the load-bearing IDENTITY the production H6 lens emits per
    :class:`ExceptionConditionCue`: the cue kind (EXCEPTION / CONDITION) and the
    normalised cue surface. This is the SAME identity :mod:`condition_exception_census`
    derives from the oracle's :func:`recognize_exception_condition_cues`, so the
    projected set is directly comparable to the production oracle for the span.

    The attachment (the construction-grammar value add) is NOT in the key: the
    production lens has no attachment (only a proximity ``scope_hint``), so keying
    on it would make every unit a superset. Attachment is the family's enrichment,
    used in the attachment-quality spot-check, not the comparison identity.
    """
    return f"{kind}:{cue}"


def projection_condexc_keys(cp: ConditionExceptionParse) -> set[str]:
    """The projected qualifier set as canonical census keys."""
    return {condexc_key(q.kind, q.cue) for q in cp.qualifiers}
