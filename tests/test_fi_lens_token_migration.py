"""Phase 7 step-2 lens→TokenTape migration assessment + deferral witnesses.

Step 1 established the TokenTape substrate and migrated ``ExceptionConditionLens``
to consume ``unit.token_tape`` at EXACT behavior identity (see
``tests/test_fi_tokentape.py``). Step 2 evaluated the four token-aligned frame
lenses for the same migration:

  * ``lenses/actor_modal.py``  → ``references/actor_modal.py``
  * ``lenses/delegation.py``   → ``references/delegation.py``
  * ``lenses/procedure.py``    → ``references/procedure.py``
  * ``lenses/sanction.py``     → ``references/sanction.py``

OUTCOME: all four are DEFERRED on ``raw_text``. The bar for migration is EXACT
behavior identity with the unchanged recognizer (the oracle), achieved by GENUINE
token-sequence matching — NOT by reconstructing the scanned text from the tape and
re-calling the recognizer (which would be a pointless non-migration). Each of the
four recognizers has a matching primitive that token-sequence matching cannot
reproduce exactly; this module pins the SPECIFIC blocker for each with a concrete
witness, so the deferral is evidenced rather than asserted, and locks the lenses on
``required_views=("raw_text",)``.

RE-ASSESSED 2026-06-17 WITH ``MorphOverlay`` AVAILABLE: the deferral was re-opened
once the source-preserving substrate gained a reverse-morphology overlay
(``unit.morph_overlay`` / ``build_morph_overlay``) that maps a tape token to the
lemma(s) of a CLOSED known-head inventory. The conclusion is UNCHANGED — the
overlay does NOT resolve any of the four blockers, for two independent reasons,
both witnessed below (``test_morph_overlay_does_not_unblock_*``):

  1. WRONG SHAPE. Every blocker is a SPAN / CHAR-OFFSET / CASE primitive, not a
     lemma-identity primitive. The overlay annotates a WHOLE ``word`` token with a
     casefolded lemma; it gives NOTHING that reconstructs a sub-token char-end
     (procedure tail cap), a ``_WORD_RE`` run that crosses tokenizer boundaries
     (sanction), case sensitivity (actor_modal keys on ``Token.normalized`` =
     casefold, and so does the overlay's lemma key), or the char-offset clause /
     gap / object / subject / trigger arithmetic all four do over ``raw_text``.
  2. WRONG VOCABULARY. The overlay's inventory is statute/structural heads
     (``laki``, ``asetus``, ``pykälä``, ``päätös`` …). It does NOT cover the actor
     registry phrases, the modal markers, the sanction stems, or most process
     nouns (``hakemus`` is not annotated). Even where it incidentally fires
     (``asetuksella`` → ``asetus`` for delegation, ``päätös`` for procedure) it
     supplies a lemma, never the missing span/offset/case primitive.

So MorphOverlay is ORTHOGONAL to every blocker: it is the wrong abstraction (lemma,
not span/case) over the wrong inventory (heads, not lens vocabularies). The four
lenses stay on ``raw_text``.

The blockers (one per recognizer; all witnessed below):

  procedure  — ``_PROCESS_RE`` matches ``(?P<stem>…)(?P<tail>[\\wäöåÄÖÅ]{0,12})``:
               the process-noun span is a stem prefix plus a tail CAPPED at 12
               word-chars WITHIN a single word. On a longer word the recognizer's
               span ends MID-TOKEN. A TokenTape token is a whole word run, so a
               token-sequence match cannot reproduce that sub-token span. (Also
               ``_DEADLINE_RE`` matches character runs spanning many tokens, e.g.
               ``viimeistään[^.;:\\n]{0,80}``.)

  sanction   — the marker span comes from ``_WORD_RE = [\\wäöåÄÖÅ]+`` (a maximal
               alphanumeric run) and the kind from ``stem in lower_word`` (substring
               anywhere in that run). ``\\w`` includes DIGITS and ``_``, which the
               Finnish tokenizer splits into separate ``word`` / ``number`` / ``punct``
               tokens. So a run like ``jos2sakko`` is ONE ``_WORD_RE`` span but THREE
               tokens — the marker span and substring classification operate on a
               unit the tape does not carry as one token.

  actor_modal — actor matching is a CASE-SENSITIVE (``re.NOFLAG``) literal alternation
               of 190 registry phrases, many MULTI-WORD and HYPHENATED
               (``liikenne- ja viestintäministeriö``), matched as contiguous raw-text
               spans. The tape's only case-insensitive key is ``Token.normalized``
               (casefold); keying on it diverges on case (``VM`` is an actor, ``vm``
               is not). Reproducing identity would force matching on ``Token.text``
               and re-encoding the literal multi-word/dash alternation as token
               sequences, after which ALL downstream work (nearest-actor pairing,
               ``REGISTRY.lookup`` ambiguity, object-span capture, the gap window)
               is char-offset arithmetic over ``raw_text`` — a cosmetic non-migration.

  delegation — same actor alternation + ``REGISTRY.lookup`` ambiguity as actor_modal,
               plus clause-window bounds, instrument/verb proximity and subject-span
               capture all computed on ``raw_text`` char offsets. The matching is
               regex-over-raw-text; token matching adds nothing reproducible.

If a future change makes any recognizer's matching genuinely token-shaped (closed
word-sequence vocabulary, no sub-token slicing, no case-sensitive literal phrases),
revisit the migration here against the same oracle-identity bar. Note that adding
MorphOverlay was NOT such a change (see the re-assessment block above): a lemma
overlay does not retire a span/offset/case blocker. The trigger for revisiting is a
change to the RECOGNIZER's matching shape, not a richer substrate view.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
)
from lawvm.core.legal_surface_tokens import MorphOverlay, TokenTape
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.lenses.actor_modal import ActorModalLens
from lawvm.finland.legal_surface.lenses.delegation import DelegationLens
from lawvm.finland.legal_surface.lenses.procedure import ProcedureLens
from lawvm.finland.legal_surface.lenses.sanction import SanctionLens
from lawvm.finland.legal_surface.tokenize import build_morph_overlay, build_token_tape
from lawvm.finland.references import actor_modal as _am
from lawvm.finland.references import procedure as _proc
from lawvm.finland.references import sanction as _sanc
from lawvm.finland.references.delegation import recognize_delegation_frames

# ---------------------------------------------------------------------------
# (1) The lenses are (correctly) still on raw_text — migration was DEFERRED.
# ---------------------------------------------------------------------------

_DEFERRED_LENSES = (
    ActorModalLens,
    DelegationLens,
    ProcedureLens,
    SanctionLens,
)


def test_deferred_lenses_still_consume_raw_text() -> None:
    for lens_cls in _DEFERRED_LENSES:
        assert lens_cls.required_views == ("raw_text",), (
            f"{lens_cls.__name__} declares required_views="
            f"{lens_cls.required_views!r}; step-2 deferred it on raw_text"
        )


# ---------------------------------------------------------------------------
# (2) Per-recognizer deferral WITNESSES — the concrete non-reproducible primitive.
# ---------------------------------------------------------------------------


def test_witness_procedure_subtoken_tail_cap() -> None:
    """``_PROCESS_RE`` ends MID-TOKEN on a long word (tail capped at 12)."""
    word = "hakemuksenkasittelyssa"  # 22 word-chars, one TokenTape token
    m = _proc._PROCESS_RE.search(word)
    assert m is not None
    # recognizer span is stem(6) + tail capped at 12 = 18, NOT the whole 22-char word
    assert (m.start(), m.end()) == (0, 18)
    assert m.end() < len(word)
    tape = build_token_tape("u", word)
    word_tokens = [t for t in tape.tokens if t.category == "word"]
    assert len(word_tokens) == 1
    # the only token spans the whole word; no token boundary at offset 18 exists
    assert word_tokens[0].char_end == len(word) != m.end()


def test_witness_procedure_deadline_spans_many_tokens() -> None:
    """``_DEADLINE_RE`` matches a character run spanning multiple tokens."""
    text = "viimeistään kolmen kuukauden kuluttua hakemuksen jättämisestä"
    m = _proc._DEADLINE_RE.search(text)
    assert m is not None
    matched = text[m.start() : m.end()]
    # the matched run crosses several whitespace-separated tokens — not a single
    # closed token-sequence the tape could match as one marker
    assert matched.split() != [matched]
    assert "viimeistään" in matched


def test_witness_sanction_word_run_crosses_token_boundaries() -> None:
    """``_WORD_RE`` marker run ≠ TokenTape tokens for alphanumeric/underscore runs."""
    text = "jos2sakko"
    runs = [w.group(0) for w in _sanc._WORD_RE.finditer(text)]
    assert runs == ["jos2sakko"]  # ONE marker run
    tape = build_token_tape("u", text)
    cats = [(t.text, t.category) for t in tape.tokens]
    # THREE tokens — the digit splits the run; the marker span and the
    # `stem in lower_word` substring classification operate on the whole run.
    assert cats == [("jos", "word"), ("2", "number"), ("sakko", "word")]


def test_witness_actor_matching_is_case_sensitive() -> None:
    """Actor alternation is case-sensitive; the tape's only key is casefold."""
    assert not (_am._ACTOR_RE.flags & re.IGNORECASE)
    upper = _am.recognize_actor_modal_frames("VM antaa asetuksen tarkemmin")
    lower = _am.recognize_actor_modal_frames("vm antaa asetuksen tarkemmin")
    # 'VM' is a registry actor; lowercase 'vm' is NOT (case-sensitive match)
    assert [f.actor_surface for f in upper.frames] == ["VM"]
    assert lower.frames == ()
    assert [r.kind for r in lower.residuals] == ["modal_without_actor"]
    # a normalized-token matcher keys on Token.normalized == text.casefold(),
    # which is identical for 'VM' and 'vm' → it cannot reproduce this distinction
    assert build_token_tape("u", "VM").tokens[0].normalized == "vm"


def test_witness_actor_alternation_has_multiword_hyphenated_phrases() -> None:
    """Actor phrases include multi-word hyphenated literals matched over raw text."""
    text = "liikenne- ja viestintäministeriö antaa asetuksen tarkemmin."
    actor_spans = [m.group(0) for m in _am._ACTOR_RE.finditer(text)]
    assert "liikenne- ja viestintäministeriö" in actor_spans
    # such a phrase tokenizes into word/dash/whitespace/word/whitespace/word —
    # matching it requires re-encoding the literal alternation as token sequences,
    # while pairing/lookup/span work stays char-offset over raw_text.
    tape = build_token_tape("u", "liikenne- ja viestintäministeriö")
    assert {t.category for t in tape.tokens} >= {"word", "dash", "whitespace"}


# ---------------------------------------------------------------------------
# (2b) MorphOverlay re-assessment WITNESSES (2026-06-17).
#
# Once the substrate gained ``build_morph_overlay``, the deferral was re-opened.
# These witnesses pin WHY the overlay does not unblock any of the four: it is the
# wrong abstraction (a casefolded whole-token lemma) over the wrong inventory
# (statute/structural heads, not the lens vocabularies). Each test ties the
# overlay's behavior to the specific primitive the corresponding lens needs.
# ---------------------------------------------------------------------------


def _overlay_lemmas(text: str) -> dict[int, tuple[str, ...]]:
    tape = build_token_tape("u", text)
    overlay = build_morph_overlay(tape)
    return {i: ann.lemmas for i, ann in overlay.annotations.items()}


def test_morph_overlay_does_not_unblock_actor_modal() -> None:
    """Overlay is casefolded + lacks actor/modal vocab → can't restore case or actors."""
    # The blocker is CASE SENSITIVITY + a 190-phrase literal alternation. The
    # overlay keys on the same casefold the tape uses, so it cannot distinguish
    # 'VM' from 'vm'; and neither the actor surface nor the modal token is in the
    # closed-head lemma inventory, so the overlay is empty for both.
    assert _overlay_lemmas("VM") == {}
    assert _overlay_lemmas("vm") == {}
    assert _overlay_lemmas("saa") == {}
    # multi-word hyphenated actor phrase: overlay annotates none of its tokens
    assert _overlay_lemmas("liikenne- ja viestintäministeriö") == {}


def test_morph_overlay_does_not_unblock_sanction() -> None:
    """Overlay annotates per-tape-token; sanction's ``_WORD_RE`` span is not a token."""
    # The blocker is a ``[\\wäöåÄÖÅ]+`` run that crosses tokenizer boundaries plus
    # a ``stem in lower_word`` substring-anywhere classification. The sanction
    # stems are not in the lemma inventory, and even if a token were annotated the
    # lemma is per-WHOLE-TOKEN — it cannot reproduce the cross-boundary run span.
    assert _overlay_lemmas("rangaistaan") == {}
    assert _overlay_lemmas("sakko") == {}
    # the digit-split run stays three tokens; the overlay annotates none of them
    assert _overlay_lemmas("jos2sakko") == {}


def test_morph_overlay_does_not_unblock_procedure() -> None:
    """Overlay gives a whole-token lemma; procedure needs a sub-token char-end."""
    # The blocker is the tail cap: ``_PROCESS_RE`` ends MID-TOKEN at offset 18 of a
    # 22-char word. A lemma annotation is attached to the WHOLE token (span 0..22),
    # so it carries no information that reconstructs the char-end at 18.
    word = "hakemuksenkasittelyssa"
    tape = build_token_tape("u", word)
    overlay = build_morph_overlay(tape)
    # 'hakemus' is not in the head inventory: no annotation at all here.
    assert dict(overlay.annotations) == {}
    # Even where the overlay DOES fire on a process noun (päätös → lemma), the
    # annotation is for the whole token and offers no sub-token span:
    paatos = _overlay_lemmas("päätös")
    assert paatos == {0: ("päätös",)}  # a lemma, not a char-offset primitive
    m = _proc._PROCESS_RE.search(word)
    assert m is not None and (m.start(), m.end()) == (0, 18)  # blocker unchanged


def test_morph_overlay_does_not_unblock_delegation() -> None:
    """Overlay may lemmatize the instrument noun but not the char-offset machinery."""
    # The blocker is char-offset clause bounds + actor alternation + REGISTRY
    # ambiguity + subject-span capture. The overlay can lemmatize 'asetuksella' →
    # 'asetus' (it is a structural head), but that lemma replaces NONE of the
    # offset arithmetic, the case-sensitive actor alternation, or the
    # registry-ambiguity resolution the recognizer performs over raw_text.
    assert _overlay_lemmas("asetuksella") == {0: ("asetus",)}
    # Some delegate-actor surfaces ARE in the head inventory (e.g. 'ministeriö' →
    # 'ministeriö'); the lemma is nonetheless useless for the recognizer's actual
    # work, which is the CASE-SENSITIVE registry alternation + REGISTRY ambiguity
    # resolution + char-offset clause/subject machinery. A lemma identity neither
    # restores case nor performs the registry lookup that types the actor.
    assert _overlay_lemmas("ministeriö") == {0: ("ministeriö",)}
    # 'valtioneuvosto' happens to be outside the head inventory entirely, so the
    # overlay is simply empty for this institutional actor.
    assert _overlay_lemmas("valtioneuvosto") == {}


# ---------------------------------------------------------------------------
# (3) Real-statute correctness anchor for the DEFERRED lenses.
#
# Even deferred, the lenses must keep producing exactly what their (unchanged)
# recognizer oracle yields when fed unit.raw_text. This asserts that identity on
# real statutes, so the deferral leaves a green, oracle-anchored baseline that a
# future genuine migration can be diffed against.
# ---------------------------------------------------------------------------


def _canonical_corpus_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "finlex.farchive").exists()


_REAL_SIDS: tuple[str, ...] = (
    "2002/723",
    "1999/731",
    "2003/434",
    "2009/916",
    "1889/39",
    "2011/379",
)


def _real_xml(sid: str) -> bytes | None:
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore

    root = os.environ["LAWVM_CANONICAL_DATA_ROOT"]
    store = TransparentCorpusStore(
        Farchive(str(Path(root) / "data" / "finlex.farchive"), readonly=True),
        cache_only=True,
    )
    try:
        return store.read_source(sid) or store.read_amendment(sid)
    finally:
        store.close()


def _bundle(unit: SourceSurfaceUnit) -> SourceSurfaceBundle:
    from lawvm.core.legal_surface_graph import SurfaceGraphSubject

    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="u",
        scope={"kind": "whole_work"},
        surface_time=None,
        source_bundle_hash="h",
        language="fi",
    )
    return SourceSurfaceBundle(jurisdiction="fi", subject=subject, units=(unit,))


def _actor_modal_oracle(raw: str) -> list[tuple]:
    scan = _am.recognize_actor_modal_frames(raw)
    out: list[tuple] = []
    for f in scan.frames:
        obj = (
            [f.object_span.byte_offset, f.object_span.byte_offset + f.object_span.byte_len]
            if f.object_span is not None
            else None
        )
        s = f.source_span
        out.append(
            (
                "frame",
                f.actor_surface,
                f.modal.token,
                f.modal.polarity,
                f.modal.voice,
                obj,
                s.byte_offset,
                s.byte_offset + s.byte_len,
            )
        )
    for r in scan.residuals:
        s = r.source_span
        out.append(("residual", r.kind, r.surface_text, s.byte_offset, s.byte_offset + s.byte_len))
    return out


def _actor_modal_lens(unit: SourceSurfaceUnit) -> list[tuple]:
    res = ActorModalLens().analyze(_bundle(unit), context=SurfaceAnalysisContext())
    out: list[tuple] = []
    for seed in res.node_seeds:
        ref = seed.source_ref
        assert ref is not None
        out.append(
            (
                "frame",
                seed.payload["actor_surface"],
                seed.payload["modal_token"],
                seed.payload["polarity"],
                seed.payload["voice"],
                seed.payload["object_span"],
                ref.char_start,
                ref.char_end,
            )
        )
    for r in res.residuals:
        ref = r.source_ref
        assert ref is not None
        out.append(
            (
                "residual",
                r.reason_code,
                r.payload["surface_text"],
                ref.char_start,
                ref.char_end,
            )
        )
    return out


def _procedure_oracle(raw: str) -> list[tuple]:
    scan = _proc.scan_procedure(raw)
    out: list[tuple] = []
    for f in scan.frames:
        s = f.source_span
        out.append(("frame", f.process_kind.value, s.byte_offset, s.byte_offset + s.byte_len))
    for r in scan.residuals:
        s = r.source_span
        out.append(("residual", r.surface_text, s.byte_offset, s.byte_offset + s.byte_len))
    return out


def _procedure_lens(unit: SourceSurfaceUnit) -> list[tuple]:
    res = ProcedureLens().analyze(_bundle(unit), context=SurfaceAnalysisContext())
    out: list[tuple] = []
    for seed in res.node_seeds:
        ref = seed.source_ref
        assert ref is not None
        out.append(("frame", seed.payload["process_kind"], ref.char_start, ref.char_end))
    for r in res.residuals:
        ref = r.source_ref
        assert ref is not None
        out.append(("residual", r.payload["surface_text"], ref.char_start, ref.char_end))
    return out


def _sanction_oracle(raw: str) -> list[tuple]:
    scan = _sanc.recognize_sanction_frames(raw)
    out: list[tuple] = []
    for f in scan.frames:
        s = f.source_span
        out.append(
            ("frame", f.sanction_kind.value, f.marker_surface, s.byte_offset, s.byte_offset + s.byte_len)
        )
    for r in scan.residuals:
        s = r.source_span
        out.append(("residual", r.kind, r.surface_text, s.byte_offset, s.byte_offset + s.byte_len))
    return out


def _sanction_lens(unit: SourceSurfaceUnit) -> list[tuple]:
    res = SanctionLens().analyze(_bundle(unit), context=SurfaceAnalysisContext())
    out: list[tuple] = []
    for seed in res.node_seeds:
        ref = seed.source_ref
        assert ref is not None
        out.append(
            ("frame", seed.payload["sanction_kind"], seed.payload["marker_surface"], ref.char_start, ref.char_end)
        )
    for r in res.residuals:
        ref = r.source_ref
        assert ref is not None
        out.append(("residual", r.reason_code, r.payload["surface_text"], ref.char_start, ref.char_end))
    return out


def _delegation_oracle(raw: str) -> list[tuple]:
    scan = recognize_delegation_frames(raw)
    out: list[tuple] = []
    for f in scan.frames:
        subj = (
            [f.subject_span.byte_offset, f.subject_span.byte_offset + f.subject_span.byte_len]
            if f.subject_span is not None
            else None
        )
        s = f.source_span
        out.append(
            (
                "frame",
                f.delegate_actor,
                f.instrument_kind,
                f.binding_strength,
                subj,
                s.byte_offset,
                s.byte_offset + s.byte_len,
            )
        )
    for r in scan.residuals:
        s = r.source_span
        out.append(("residual", r.kind, r.surface_text, s.byte_offset, s.byte_offset + s.byte_len))
    return out


def _delegation_lens(unit: SourceSurfaceUnit) -> list[tuple]:
    res = DelegationLens().analyze(_bundle(unit), context=SurfaceAnalysisContext())
    out: list[tuple] = []
    for seed in res.node_seeds:
        ref = seed.source_ref
        assert ref is not None
        out.append(
            (
                "frame",
                seed.payload["delegate_actor"],
                seed.payload["instrument_kind"],
                seed.payload["binding_strength"],
                seed.payload["subject_span"],
                ref.char_start,
                ref.char_end,
            )
        )
    for r in res.residuals:
        ref = r.source_ref
        assert ref is not None
        out.append(("residual", r.reason_code, r.payload["surface_text"], ref.char_start, ref.char_end))
    return out


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_deferred_lenses_match_oracle_on_real_statutes() -> None:
    checked = 0
    for sid in _REAL_SIDS:
        xb = _real_xml(sid)
        if not xb:
            continue
        bundle = build_surface_bundle(xb, sid)
        unit = bundle.units[0]
        # the substrate is populated on real units (sanity for the deferral
        # context): BOTH the tape AND the morph overlay are available here, so the
        # oracle-identity baseline below is anchored with the richer substrate in
        # scope — the lenses still consume raw_text because the overlay does not
        # retire any blocker (see the (2b) MorphOverlay witnesses).
        assert isinstance(unit.token_tape, TokenTape)
        assert isinstance(unit.morph_overlay, MorphOverlay)
        raw = unit.raw_text
        assert _actor_modal_lens(unit) == _actor_modal_oracle(raw), f"actor_modal {sid}"
        assert _delegation_lens(unit) == _delegation_oracle(raw), f"delegation {sid}"
        assert _procedure_lens(unit) == _procedure_oracle(raw), f"procedure {sid}"
        assert _sanction_lens(unit) == _sanction_oracle(raw), f"sanction {sid}"
        checked += 1
        if checked >= 5:
            break
    assert checked >= 5, f"needed ≥5 real statutes, got {checked}"
