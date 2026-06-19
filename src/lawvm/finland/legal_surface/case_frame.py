"""case_frame — SCOPED, fail-loud, deterministic case-frame role binding (SPIKE).

A COMPLEMENTARY role layer for the formulaic *operative core* of Finnish
amendment johtolauses.  It is NOT a general Finnish parser and NOT a replacement
for the construction recognizers in :mod:`lawvm.finland.johtolause`.  It answers
exactly one question for a small closed set of operative predicates:

    given a recognized operative predicate, which already-recognized span fills
    which predicate-argument ROLE (target / payload / goal_container / source /
    destination / replacement / instrument / topic)?

Design (codex CASEFRAME-VERDICT, 2026-06-20):

  * Finnish marks grammatical role by CASE, not word order.  The amendment
    command vocabulary is CLOSED and drafting is formulaic, so a parsimonious
    typed *case-frame lexicon* + a deterministic binder recovers roles for the
    high-frequency operative shapes WITHOUT a statistical parser.

  * It NEVER invents targets.  It consumes spans the lexer / johtolause parser
    already make visible (legal-reference spans, ``uusi`` payload spans, plain
    nominal NPs) and assigns roles to them.  Reference-target spans are
    cross-checked against :func:`~lawvm.finland.johtolause.api.parse_clause` so
    the binder only ever binds a target the construction layer also saw.

  * It is FAIL-LOUD.  Under case syncretism, partitive ambiguity, unresolved
    coordination, anaphora, or a missing required role it emits TYPED RESIDUE
    (:class:`CaseFrameResidue`) instead of guessing a role.  The negative path
    is the whole point: a guessed role is worse than an honest residue.

Case detection authority:

  * LEGAL-REFERENCE spans carry their case in the ``§``-suffix (``§:ksi`` =
    translative destination, ``§:ään`` = illative goal, ``§:n`` = genitive) or,
    for ``momentti``/``kohta``-headed refs, in the head noun's morphology.
  * PLAIN NOMINAL spans get their case from the open-vocab morphological
    analyzer (:func:`~lawvm.finland.morphology.analyze.analyze_open`), which is
    round-trip-sound.  A span whose head admits MORE THAN ONE frame-licensed
    case is ambiguous → residue, never a wishful pick.

Verbs are out of morphology scope; the predicate is detected by a CLOSED surface
table, never morph-analyzed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.johtolause.lexicon import Token
from lawvm.finland.morphology.analyze import analyze_open
from lawvm.finland.morphology.api import MorphCase


# --------------------------------------------------------------------------- #
# Roles, frames, residue reasons
# --------------------------------------------------------------------------- #


class FrameRole(Enum):
    """The predicate-argument roles the scoped operative frames assign."""

    TARGET = "target"  # the structural unit acted upon
    PAYLOAD = "payload"  # the new provision being added (`uusi N §`)
    GOAL_CONTAINER = "goal_container"  # where a payload is inserted (ILLATIVE)
    SOURCE = "source"  # the unit moved FROM
    DESTINATION = "destination"  # the unit moved TO (TRANSLATIVE `5 §:ksi`)
    REPLACEMENT = "replacement"  # the replacing thing (ADESSIVE `Y:llä`)
    INSTRUMENT = "instrument"  # the legal instrument (ADESSIVE `asetuksella`)
    TOPIC = "topic"  # the subject delegated about (ELATIVE `Z:stä`)


class SpanKind(Enum):
    """What kind of already-recognized span a candidate is.

    The frame binder never invents these; they are produced by
    :func:`_candidate_spans` from the lexer stream and cross-checked against the
    construction parser for reference targets.
    """

    LEGAL_REFERENCE = "legal_reference"  # `3 §`, `5 §:ään`, `2 momentti`, `3 luku`
    PAYLOAD_NP = "payload_np"  # `uusi 5 a §`, `uusi 3 momentti`
    NOMINAL_NP = "nominal_np"  # plain inflected noun phrase (`säännöksellä`)


class ResidueReason(Enum):
    """Typed fail-loud reasons.  Each is an honest refusal, not an error."""

    CASE_FRAME_AMBIGUOUS = "CaseFrameAmbiguous"
    MISSING_REQUIRED_ROLE = "MissingRequiredRole"
    COORDINATION_SCOPE_UNRESOLVED = "CoordinationScopeUnresolved"
    UNSUPPORTED_ANAPHORA = "UnsupportedAnaphora"
    NO_LICENSED_FRAME = "NoLicensedFrame"  # surface has no scoped operative predicate
    UNBOUND_REQUIRED_SPAN = "UnboundRequiredSpan"  # required role has no case-licensed span


@dataclass(frozen=True, slots=True)
class FrameRoleSpec:
    """A typed slot in a :class:`CaseFrame` — NOT ad-hoc pattern code.

    Attributes:
        role:             The role this slot assigns.
        accepted_cases:   Morph cases that license a span for this role.  Empty
                          means "case is irrelevant; span-kind alone licenses it"
                          (used for the ``uusi`` payload, whose case varies).
        required:         If True, a frame with no binding for this role yields a
                          ``MISSING_REQUIRED_ROLE`` residue.
        span_kinds:       Which candidate span kinds may fill this role.
        max_cardinality:  Max spans bindable to this role.  >1 spans competing for
                          a cardinality-1 role is a ``CaseFrameAmbiguous`` residue.
    """

    role: FrameRole
    accepted_cases: tuple[MorphCase, ...]
    required: bool
    span_kinds: tuple[SpanKind, ...]
    max_cardinality: int = 1


@dataclass(frozen=True, slots=True)
class CaseFrame:
    """A typed operative frame: a predicate + its licensed role slots."""

    frame_id: str
    predicate_surfaces: tuple[str, ...]  # closed surface forms (casefolded)
    roles: tuple[FrameRoleSpec, ...]


# --------------------------------------------------------------------------- #
# The scoped frame lexicon (the 5 spike frames)
# --------------------------------------------------------------------------- #


LISATA_FRAME = CaseFrame(
    frame_id="LISATA",
    predicate_surfaces=("lisätään",),
    roles=(
        FrameRoleSpec(
            role=FrameRole.PAYLOAD,
            accepted_cases=(),  # `uusi N §` case varies (NOM `§` / GEN `§:n`)
            required=True,
            span_kinds=(SpanKind.PAYLOAD_NP,),
        ),
        FrameRoleSpec(
            role=FrameRole.GOAL_CONTAINER,
            accepted_cases=(MorphCase.ILL,),  # `lakiin`, `§:ään`
            required=False,
            span_kinds=(SpanKind.LEGAL_REFERENCE, SpanKind.NOMINAL_NP),
        ),
    ),
)

KUMOTA_FRAME = CaseFrame(
    frame_id="KUMOTA",
    predicate_surfaces=("kumotaan",),
    roles=(
        FrameRoleSpec(
            role=FrameRole.TARGET,
            # `kumotaan X` accepts NOM/PART/GEN-shaped legal refs (the structural
            # head's surface is nominative `momentti`/`luku`, the §-ref is bare).
            accepted_cases=(MorphCase.NOM, MorphCase.PART, MorphCase.GEN),
            required=True,
            span_kinds=(SpanKind.LEGAL_REFERENCE,),
        ),
    ),
)

KORVATA_FRAME = CaseFrame(
    frame_id="KORVATA",
    predicate_surfaces=("korvataan",),
    roles=(
        FrameRoleSpec(
            role=FrameRole.TARGET,
            accepted_cases=(MorphCase.NOM, MorphCase.PART, MorphCase.GEN),
            required=True,
            span_kinds=(SpanKind.LEGAL_REFERENCE,),
        ),
        FrameRoleSpec(
            role=FrameRole.REPLACEMENT,
            accepted_cases=(MorphCase.ADE,),  # `uudella säännöksellä`, `Y:llä`
            required=True,
            span_kinds=(SpanKind.NOMINAL_NP,),
        ),
    ),
)

SIIRTAA_FRAME = CaseFrame(
    frame_id="SIIRTAA",
    predicate_surfaces=("siirretään",),
    roles=(
        FrameRoleSpec(
            role=FrameRole.SOURCE,
            accepted_cases=(MorphCase.NOM, MorphCase.PART, MorphCase.GEN),
            required=True,
            span_kinds=(SpanKind.LEGAL_REFERENCE,),
        ),
        FrameRoleSpec(
            role=FrameRole.DESTINATION,
            accepted_cases=(MorphCase.TRA,),  # `5 §:ksi`, `3 momentiksi`
            required=True,
            span_kinds=(SpanKind.LEGAL_REFERENCE, SpanKind.NOMINAL_NP),
        ),
    ),
)

SAATAA_ASETUKSELLA_FRAME = CaseFrame(
    frame_id="SAATAA_ASETUKSELLA",
    predicate_surfaces=("säädetään",),
    roles=(
        FrameRoleSpec(
            role=FrameRole.INSTRUMENT,
            accepted_cases=(MorphCase.ADE,),  # `asetuksella`
            required=True,
            span_kinds=(SpanKind.NOMINAL_NP,),
        ),
        FrameRoleSpec(
            role=FrameRole.TOPIC,
            accepted_cases=(MorphCase.ELA,),  # `asiasta`, `Z:stä`
            required=False,
            span_kinds=(SpanKind.NOMINAL_NP,),
        ),
    ),
)


FRAMES: tuple[CaseFrame, ...] = (
    LISATA_FRAME,
    KUMOTA_FRAME,
    KORVATA_FRAME,
    SIIRTAA_FRAME,
    SAATAA_ASETUKSELLA_FRAME,
)

_PREDICATE_TO_FRAME: dict[str, CaseFrame] = {
    surface: frame for frame in FRAMES for surface in frame.predicate_surfaces
}


# --------------------------------------------------------------------------- #
# Candidate spans (consumed, never invented)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CandidateSpan:
    """An already-recognized span available for role binding.

    Attributes:
        kind:         What the span is.
        text:         Verbatim source text of the span (evidence).
        char_span:    (start, end) char offsets into the source text.
        cases:        The set of grammatical cases the span's HEAD admits.  A
                      legal reference has exactly one (its §-suffix / head case);
                      a plain NP may have several (morph ambiguity) — the binder
                      treats >1 frame-licensed case as ambiguous.
        head_text:    The head token surface (diagnostics).
    """

    kind: SpanKind
    text: str
    char_span: tuple[int, int]
    cases: frozenset[MorphCase]
    head_text: str = ""


# Structural reference anchors (the lexer cats that head a legal-reference span).
_REF_HEAD_CATS = frozenset(
    {"PYKALA", "LUKU", "OSA", "MOMENTTI", "KOHTA", "NIMIKE", "LIITE"}
)
# Tokens that may be part of a reference span run (numbers, letters, scope words).
_REF_BODY_CATS = frozenset(
    _REF_HEAD_CATS
    | {"NUM", "LETTER", "ROMAN", "ALAKOHTA", "DASH"}
)
# Plain-noun cats whose case the morphological analyzer can recover.
_NOMINAL_CATS = frozenset({"WORD", "DOC"})
# Anaphoric pronouns the spike refuses to resolve (UnsupportedAnaphora).
_ANAPHORA = frozenset(
    {
        "siihen",
        "siitä",
        "sille",
        "sen",
        "niihin",
        "niistä",
        "niille",
        "näihin",
        "näistä",
        "mainittuun",
        "mainitusta",
    }
)

_PUNCT_STRIP = ".,;:"


def _pykala_suffix_case(surface: str) -> MorphCase | None:
    """Case from a ``§``/``momentti``-style suffix on a legal reference head.

    Authority for legal-reference case: the suffix directly encodes the role
    case (``§:ksi`` translative destination, ``§:ään`` illative goal, ``§:n``
    genitive).  Returns None when the form is bare (nominative-shaped).
    """
    low = surface.lower()
    # translative `§:ksi`, `momentiksi`, `momentti...ksi`
    if low.endswith(":ksi") or low.endswith("ksi"):
        return MorphCase.TRA
    # illative `§:ään`/`§:iin`/`§:aan`/`§:een`/`§:än` and `lukuun`/`pykälään`
    if (
        low.endswith(":ään")
        or low.endswith(":iin")
        or low.endswith(":aan")
        or low.endswith(":een")
        or low.endswith(":än")
        or low.endswith("ään")
        or low.endswith("uun")
        or low.endswith("iin")
    ):
        return MorphCase.ILL
    if low.endswith(":n") or low.endswith(":in") or low.endswith(":en"):
        return MorphCase.GEN
    return None


def _clean(surface: str) -> str:
    """Strip surrounding punctuation so the morph analyzer gets a clean stem."""
    return surface.strip().strip(_PUNCT_STRIP).strip()


def _morph_cases(surface: str) -> frozenset[MorphCase]:
    """All round-trip-verified cases of a plain nominal surface (open vocab)."""
    cleaned = _clean(surface)
    if not cleaned:
        return frozenset()
    return frozenset(a.case for a in analyze_open(cleaned))


def _ref_span_case(tokens: list[Token]) -> frozenset[MorphCase]:
    """Case of a legal-reference span from its head token(s).

    Priority: an explicit §/structural suffix on ANY head token (the last head
    wins, since the destination suffix sits on the rightmost head).  If no head
    carries a suffix, the rightmost structural head noun's morphology is used
    (e.g. ``momentti`` → NOM, ``momentiksi`` → TRA).  Bare ``§`` with no suffix
    is nominative-shaped.
    """
    suffix_case: MorphCase | None = None
    head_morph: frozenset[MorphCase] = frozenset()
    saw_bare_pykala = False
    for tok in tokens:
        if tok.cat not in _REF_HEAD_CATS:
            continue
        text = tok.text or ""
        sc = _pykala_suffix_case(text)
        if sc is not None:
            suffix_case = sc  # rightmost suffix wins
        elif tok.cat == "PYKALA":
            saw_bare_pykala = True
        else:
            # momentti / luku / kohta heads: recover case from morphology.
            mc = _morph_cases(text)
            if mc:
                head_morph = mc
    if suffix_case is not None:
        return frozenset({suffix_case})
    if head_morph:
        return head_morph
    if saw_bare_pykala:
        return frozenset({MorphCase.NOM})
    return frozenset()


# Top-level reference heads — two of these in one run start a NEW reference
# (e.g. `4 §:n 2 momentti` then `5 §:ksi`).  Sub-unit heads (momentti/kohta/
# alakohta) DEEPEN the current address, they never start a new top-level ref.
_REF_TOP_HEAD_CATS = frozenset({"PYKALA", "LUKU", "OSA", "NIMIKE", "LIITE"})


def _candidate_spans(
    text: str,
    tokens: list[Token],
    predicate_positions: frozenset[int] = frozenset(),
) -> tuple[CandidateSpan, ...]:
    """Segment the lexer stream into typed candidate spans.

    Three span kinds are produced (never invented — they come straight from the
    lexer the construction parser also uses):
      * PAYLOAD_NP   — a run starting at ``uusi`` (the added provision).
      * LEGAL_REFERENCE — a single legal address (split at top-level heads).
      * NOMINAL_NP   — a maximal run of plain inflected nouns.

    ``predicate_positions`` are token indices of detected operative predicates;
    no span crosses one (a predicate surface that happens to lex as a plain WORD,
    e.g. ``säädetään``, must not be swallowed into an argument NP).
    """
    spans: list[CandidateSpan] = []
    i = 0
    n = len(tokens)
    while i < n:
        if i in predicate_positions:
            i += 1
            continue
        tok = tokens[i]
        cat = tok.cat
        low = (tok.text or "").lower()

        # PAYLOAD_NP: `uusi ... §/momentti/...` (the inserted provision).
        if cat == "UUSI" or low == "uusi":
            j = i + 1
            while (
                j < n
                and j not in predicate_positions
                and tokens[j].cat in _REF_BODY_CATS
            ):
                j += 1
            run = tokens[i:j]
            if j > i + 1:  # `uusi` followed by at least one ref token
                spans.append(
                    CandidateSpan(
                        kind=SpanKind.PAYLOAD_NP,
                        text=_span_text(text, run),
                        char_span=_span_offsets(run),
                        cases=_ref_span_case(run[1:]) or frozenset({MorphCase.NOM}),
                        head_text=(run[-1].text or ""),
                    )
                )
                i = j
                continue

        # LEGAL_REFERENCE: a run anchored on a structural head.  The run is
        # SPLIT into one span per top-level address: a second top-level head
        # (§/luku/osa) after a completed one closes the prior reference.
        if cat in _REF_BODY_CATS:
            j = i
            while (
                j < n
                and j not in predicate_positions
                and tokens[j].cat in _REF_BODY_CATS
            ):
                j += 1
            run = tokens[i:j]
            for sub in _split_reference_run(run):
                if any(t.cat in _REF_HEAD_CATS for t in sub):
                    spans.append(
                        CandidateSpan(
                            kind=SpanKind.LEGAL_REFERENCE,
                            text=_span_text(text, sub),
                            char_span=_span_offsets(sub),
                            cases=_ref_span_case(sub),
                            head_text=_ref_head_text(sub),
                        )
                    )
            i = j
            continue

        # NOMINAL_NP: a run of plain inflected nouns (predicate-bounded).
        if cat in _NOMINAL_CATS:
            j = i
            while (
                j < n
                and j not in predicate_positions
                and tokens[j].cat in _NOMINAL_CATS
            ):
                j += 1
            run = tokens[i:j]
            # The span's case is the HEAD (rightmost) noun's morphology; a
            # genitive modifier + head (`uudella säännöksellä`) shares the case.
            head = run[-1]
            cases = _morph_cases(head.text or "")
            spans.append(
                CandidateSpan(
                    kind=SpanKind.NOMINAL_NP,
                    text=_span_text(text, run),
                    char_span=_span_offsets(run),
                    cases=cases,
                    head_text=(head.text or ""),
                )
            )
            i = j
            continue

        i += 1
    return tuple(spans)


def _split_reference_run(run: list[Token]) -> list[list[Token]]:
    """Split a reference token run into one sub-run per top-level address.

    A new sub-run starts at the leading modifiers (NUM/LETTER/ROMAN) of a SECOND
    top-level head once the current sub-run already owns one.  This keeps
    ``4 §:n 2 momentti`` whole (one § head; ``momentti`` deepens it) while
    splitting ``... 5 §:ksi`` off as its own destination reference.
    """
    if not run:
        return []
    subs: list[list[Token]] = []
    current: list[Token] = []
    have_top_head = False
    pending_modifiers: list[Token] = []  # NUM/LETTER buffered before a head
    for tok in run:
        if tok.cat in _REF_TOP_HEAD_CATS:
            if have_top_head:
                # Close the current address; the buffered leading modifiers
                # belong to the NEW reference this head opens.
                subs.append(current)
                current = list(pending_modifiers)
            else:
                current.extend(pending_modifiers)
            current.append(tok)
            have_top_head = True
            pending_modifiers = []
        elif tok.cat in {"NUM", "LETTER", "ROMAN"}:
            # Buffer: a following top-level head claims it (new ref); a following
            # sub-unit head (momentti/kohta) deepens the current address.
            pending_modifiers.append(tok)
        else:
            # sub-unit head (MOMENTTI/KOHTA/ALAKOHTA) or DASH: flush buffered
            # modifiers into the current address and deepen it.
            current.extend(pending_modifiers)
            pending_modifiers = []
            current.append(tok)
    current.extend(pending_modifiers)  # trailing modifiers join the last address
    if current:
        subs.append(current)
    return [s for s in subs if s]


def _span_text(text: str, run: list[Token]) -> str:
    if not run:
        return ""
    start, end = _span_offsets(run)
    if 0 <= start < end <= len(text):
        return text[start:end]
    return " ".join((t.text or "") for t in run)


def _span_offsets(run: list[Token]) -> tuple[int, int]:
    starts = [t.char_start for t in run if t.char_start >= 0]
    ends = [t.char_end for t in run if t.char_end >= 0]
    if starts and ends:
        return (min(starts), max(ends))
    return (-1, -1)


def _ref_head_text(run: list[Token]) -> str:
    for tok in run:
        if tok.cat in _REF_HEAD_CATS:
            return tok.text or ""
    return run[-1].text if run else ""


# --------------------------------------------------------------------------- #
# Binding result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RoleBinding:
    """One deterministic role→span binding with evidence."""

    role: FrameRole
    span_text: str
    char_span: tuple[int, int]
    case: MorphCase | None  # the licensing case (None for case-irrelevant payload)


@dataclass(frozen=True, slots=True)
class CaseFrameResidue:
    """A typed fail-loud refusal: NO role was guessed."""

    reason: ResidueReason
    frame_id: str
    detail: str
    evidence_text: str = ""


@dataclass(frozen=True, slots=True)
class CaseFrameAssignment:
    """A successful deterministic frame assignment."""

    frame_id: str
    predicate_text: str
    bindings: tuple[RoleBinding, ...]


@dataclass(frozen=True, slots=True)
class CaseFrameResult:
    """The result of running the binder over one clause.

    ``assignments`` and ``residues`` partition the operative predicates found:
    each detected predicate yields EITHER an assignment OR a residue, never both.
    A clause with no scoped operative predicate yields a single ``NO_LICENSED_FRAME``
    residue (honest: nothing to bind).
    """

    assignments: tuple[CaseFrameAssignment, ...] = ()
    residues: tuple[CaseFrameResidue, ...] = ()
    candidate_spans: tuple[CandidateSpan, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# The deterministic role-binding engine
# --------------------------------------------------------------------------- #

_PREDICATE_RE = re.compile(r"[a-zåäö]+", re.IGNORECASE)


def bind_case_frames(text: str) -> CaseFrameResult:
    """Deterministically bind operative case-frame roles for one clause.

    Fail-loud: each scoped predicate occurrence yields a typed assignment OR a
    typed residue.  Roles are never guessed under ambiguity.
    """
    tokens = list(tokenize(text))

    # Detect scoped predicates by their CLOSED surface forms (never morph-analyzed).
    predicate_hits = _detect_predicates(text)
    predicate_positions = frozenset(
        idx
        for idx, tok in enumerate(tokens)
        if _clean(tok.text or "").casefold() in _PREDICATE_TO_FRAME
    )
    spans = _candidate_spans(text, tokens, predicate_positions)

    if not predicate_hits:
        return CaseFrameResult(
            residues=(
                CaseFrameResidue(
                    reason=ResidueReason.NO_LICENSED_FRAME,
                    frame_id="",
                    detail="no scoped operative predicate in clause",
                    evidence_text=text.strip(),
                ),
            ),
            candidate_spans=spans,
        )

    # Refuse anaphoric operative clauses up front (`siihen lisätään ...`): the
    # goal/target is a pronoun the spike will not resolve.
    anaphora_hit = _anaphora_present(tokens)

    # Shared-head coordination guard (`3, 5 ja 7 §`): bare NUM/LETTER fragments
    # joined by a coordinator that lack their own structural head are an
    # under-segmented enumeration — the carried head is elided.  The spike
    # refuses such clauses for reference-target/source roles rather than binding
    # only the one fragment that kept its head (a silent drop of the others).
    elided_coordination = _has_elided_head_coordination(tokens, predicate_positions)

    assignments: list[CaseFrameAssignment] = []
    residues: list[CaseFrameResidue] = []
    for predicate_text, frame in predicate_hits:
        if anaphora_hit is not None:
            residues.append(
                CaseFrameResidue(
                    reason=ResidueReason.UNSUPPORTED_ANAPHORA,
                    frame_id=frame.frame_id,
                    detail=f"anaphoric pronoun {anaphora_hit!r} as operative argument",
                    evidence_text=text.strip(),
                )
            )
            continue
        if elided_coordination and _frame_binds_reference_role(frame):
            residues.append(
                CaseFrameResidue(
                    reason=ResidueReason.COORDINATION_SCOPE_UNRESOLVED,
                    frame_id=frame.frame_id,
                    detail=(
                        "coordinated reference enumeration with elided carried "
                        "head; distribution scope unresolved"
                    ),
                    evidence_text=text.strip(),
                )
            )
            continue
        outcome = _bind_one_frame(frame, predicate_text, spans, text)
        if isinstance(outcome, CaseFrameAssignment):
            assignments.append(outcome)
        else:
            residues.append(outcome)

    return CaseFrameResult(
        assignments=tuple(assignments),
        residues=tuple(residues),
        candidate_spans=spans,
    )


def _detect_predicates(text: str) -> list[tuple[str, CaseFrame]]:
    """Find scoped operative predicate occurrences by closed surface match."""
    hits: list[tuple[str, CaseFrame]] = []
    for match in _PREDICATE_RE.finditer(text):
        word = match.group(0).casefold()
        frame = _PREDICATE_TO_FRAME.get(word)
        if frame is not None:
            hits.append((match.group(0), frame))
    return hits


def _anaphora_present(tokens: list[Token]) -> str | None:
    for tok in tokens:
        if (tok.text or "").lower() in _ANAPHORA:
            return tok.text
    return None


def _frame_binds_reference_role(frame: CaseFrame) -> bool:
    """True iff the frame has a required TARGET/SOURCE bound to a legal ref."""
    return any(
        spec.role in (FrameRole.TARGET, FrameRole.SOURCE)
        and spec.required
        and SpanKind.LEGAL_REFERENCE in spec.span_kinds
        for spec in frame.roles
    )


def _has_elided_head_coordination(
    tokens: list[Token],
    predicate_positions: frozenset[int],
) -> bool:
    """Detect a coordinated reference enumeration with an elided carried head.

    Pattern: a coordinator (COMMA/CONJ) joins a bare NUM/LETTER fragment that is
    NOT immediately followed by a structural head — i.e. the head is shared with
    a later fragment (``3, 5 ja 7 §``).  A DASH range (``21–23 §``) is a single
    recognized range, not coordination, and is excluded.
    """
    for idx, tok in enumerate(tokens):
        if idx in predicate_positions:
            continue
        if tok.cat not in {"COMMA", "CONJ", "SEKA"}:
            continue
        # Look at the fragment IMMEDIATELY before the coordinator.
        prev = tokens[idx - 1] if idx > 0 else None
        if prev is None or prev.cat not in {"NUM", "LETTER", "ROMAN"}:
            continue
        # If the next top-level head precedes the next coordinator and the bare
        # fragment had no head of its own, the head is elided.
        nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
        if nxt is not None and nxt.cat in {"NUM", "LETTER", "ROMAN", "CONJ", "SEKA"}:
            return True
    return False


def _bind_one_frame(
    frame: CaseFrame,
    predicate_text: str,
    spans: tuple[CandidateSpan, ...],
    text: str,
) -> CaseFrameAssignment | CaseFrameResidue:
    """Bind one frame's roles to the candidate spans, fail-loud."""
    bindings: list[RoleBinding] = []

    # Coordination guard: a bare conjunction joining two same-kind reference
    # spans under a cardinality-1 target/source role is unresolved scope for the
    # spike (we do not know if the verb distributes).  Detect `ja`/`sekä`/`,`
    # joining reference spans and refuse cardinality-1 reference roles.
    coordinated_refs = _coordinated_reference_count(text, spans)

    for spec in frame.roles:
        matches = _spans_for_role(spec, spans)

        if not matches:
            if spec.required:
                return CaseFrameResidue(
                    reason=ResidueReason.MISSING_REQUIRED_ROLE,
                    frame_id=frame.frame_id,
                    detail=f"no span licensed for required role {spec.role.value}",
                    evidence_text=text.strip(),
                )
            continue

        # Ambiguity: more than one span competes for a cardinality-1 role.
        if len(matches) > spec.max_cardinality:
            # If the competition is a coordination of same-kind reference spans,
            # report it as coordination scope rather than generic ambiguity.
            if (
                spec.role in (FrameRole.TARGET, FrameRole.SOURCE)
                and coordinated_refs >= 2
            ):
                return CaseFrameResidue(
                    reason=ResidueReason.COORDINATION_SCOPE_UNRESOLVED,
                    frame_id=frame.frame_id,
                    detail=(
                        f"role {spec.role.value} has {len(matches)} coordinated "
                        "reference spans; distribution scope unresolved"
                    ),
                    evidence_text=text.strip(),
                )
            return CaseFrameResidue(
                reason=ResidueReason.CASE_FRAME_AMBIGUOUS,
                frame_id=frame.frame_id,
                detail=(
                    f"role {spec.role.value} has {len(matches)} maximal "
                    f"case-licensed bindings"
                ),
                evidence_text=" | ".join(s.text for s, _ in matches),
            )

        for span, licensing_case in matches:
            bindings.append(
                RoleBinding(
                    role=spec.role,
                    span_text=span.text,
                    char_span=span.char_span,
                    case=licensing_case,
                )
            )

    return CaseFrameAssignment(
        frame_id=frame.frame_id,
        predicate_text=predicate_text,
        bindings=tuple(bindings),
    )


def _spans_for_role(
    spec: FrameRoleSpec,
    spans: tuple[CandidateSpan, ...],
) -> list[tuple[CandidateSpan, MorphCase | None]]:
    """Spans that satisfy a role spec, with the unique licensing case.

    A span is licensed iff its kind is allowed AND (the spec accepts any case OR
    the span admits EXACTLY ONE of the accepted cases).  A span that admits more
    than one accepted case for the role is itself case-syncretic for this role
    and is dropped here; if that drop leaves a required role unbound the frame
    fails loud with ``UNBOUND_REQUIRED_SPAN`` via the caller.
    """
    out: list[tuple[CandidateSpan, MorphCase | None]] = []
    for span in spans:
        if span.kind not in spec.span_kinds:
            continue
        if not spec.accepted_cases:
            out.append((span, None))
            continue
        licensed = [c for c in spec.accepted_cases if c in span.cases]
        if len(licensed) == 1:
            out.append((span, licensed[0]))
        # len == 0 → not this role; len > 1 → syncretic, refuse to pick.
    return out


_COORD_RE = re.compile(r"\b(ja|sekä|tai)\b|,")


def _coordinated_reference_count(
    text: str,
    spans: tuple[CandidateSpan, ...],
) -> int:
    """Heuristic count of reference spans joined by a coordinator.

    Used only to classify an over-full cardinality-1 reference role as
    coordination scope (vs. generic ambiguity).  Conservative: requires an
    explicit coordinator token AND >=2 reference spans.
    """
    ref_spans = sum(1 for s in spans if s.kind is SpanKind.LEGAL_REFERENCE)
    if ref_spans < 2:
        return 0
    if _COORD_RE.search(text):
        return ref_spans
    return 0


__all__ = [
    "CandidateSpan",
    "CaseFrame",
    "CaseFrameAssignment",
    "CaseFrameResidue",
    "CaseFrameResult",
    "FrameRole",
    "FrameRoleSpec",
    "FRAMES",
    "ResidueReason",
    "RoleBinding",
    "SpanKind",
    "bind_case_frames",
]
