"""T1 adjudication of an HE payload divergence — types the witness_disagreement tail.

Phase 2's op-structure is solved (op_missing ≈ 0) and the shared inert-encoding quotient
(:mod:`lawvm.finland.op_equivalence`) folds the unarguably-inert typographic classes. What
SURVIVES as ``payload_mismatch`` is the residual the objective says to ADJUDICATE, not to
force into a "defect" bucket: for each residual body pair (trusted XML proposed body vs the
PDF-extracted proposed body) exactly one of

  * ``ORACLE_ARTIFACT``     — the XML oracle carries an error the PDF gets right (e.g. a
                              run-together "verovirastontai" that IS literally in the XML,
                              PDF correctly "veroviraston tai"). First-class
                              witness_disagreement → recorded as ``oracle_suspect``; per the
                              objective this COUNTS as an accounted (done) unit, not a defect.
  * ``READER_DEFECT``       — the PDF text layer garbled a word the XML gets right (e.g. an
                              old-PDF glyph misread "johdosta"→"jo/ulosta"). A reader defect
                              to fix/route to a higher-fidelity read; NOT witness_disagreement.
  * ``SEGMENTATION_NOISE``  — the two bodies differ ONLY because the PDF reader's body BOUNDARY
                              over-captured layout furniture that is not part of the provision
                              body at all: a signature block, a running header / page number
                              ("HE 58/1995 vp 12"), or a trailing dashline / whitespace run
                              ("— — —"). NOT a glyph misread and NOT a witness disagreement —
                              the XML has no "error"; the PDF just segmented the body wrong. A
                              reader body-boundary defect to fix; must NOT inflate the accounted
                              witness_disagreement bucket (a prior run's oracle_artifact tail was
                              ~1/3 exactly this, mislabelled as if the XML were wrong).
  * ``GENUINE_DIFFERENCE``  — both are valid Finnish and the two witnesses genuinely differ
                              (a real editorial divergence between the HE's XML and PDF).
                              First-class witness_disagreement → recorded.
  * ``EQUIVALENT``          — the bodies are the same modulo a typographic class the quotient
                              does not YET fold: a discovery-loop signal to GRADUATE a new
                              inert fold in ``op_equivalence`` (not to adjudicate by hand).
  * ``UNCERTAIN``           — the local model would not commit; escalate to the terminal image
                              tier (Opus) rather than guess.

This module owns ONLY the FI-specific prompt + verdict typing; the LLM transport is injected
as a ``chat_fn`` (real use wires it to
:class:`lawvm.ingest.llm_backends.llm_adjudicator.LlmWorkflowAdjudicator`), so the classifier
is hermetically testable with a scripted fake and the determinism firewall stays at the
transport/cache layer. The verdict is a pure function of (left, right, model) — cache it by
the two body digests + adjudicator id (no stale reads).
"""
from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Callable


class DivergenceVerdict(StrEnum):
    """The closed set of adjudicated outcomes for a surviving payload divergence."""

    ORACLE_ARTIFACT = "oracle_artifact"  # XML wrong, PDF right → oracle_suspect (witness_disagreement)
    READER_DEFECT = "reader_defect"  # PDF garbled, XML right → reader defect (fix / re-read)
    SEGMENTATION_NOISE = "segmentation_noise"  # PDF over-captured layout furniture (boundary defect); NOT witness_disagreement
    GENUINE_DIFFERENCE = "genuine_difference"  # both valid, genuinely differ → witness_disagreement
    EQUIVALENT = "equivalent"  # inert-only, quotient gap → graduate a fold
    UNCERTAIN = "uncertain"  # model would not commit → escalate to terminal image tier

    @property
    def is_witness_disagreement(self) -> bool:
        """True for the outcomes that are first-class witness_disagreement (accounted/done).

        A genuine XML-vs-PDF difference and an oracle artifact are both real divergences
        between the two witnesses that are NOT a reconstruction defect — the objective
        records these as done, never counts them as an un-verified defect.
        """
        return self in (
            DivergenceVerdict.ORACLE_ARTIFACT,
            DivergenceVerdict.GENUINE_DIFFERENCE,
        )


_ADJUDICATION_SYSTEM = (
    "You are a meticulous Finnish statutory-text adjudicator. You are given TWO versions of "
    "the SAME proposed statutory provision body from a government bill (hallituksen esitys): "
    "version A is the trusted XML oracle, version B is text extracted from the bill PDF. They "
    "differ after inert typographic normalization. Decide the SINGLE best cause of the "
    "difference. Reply with EXACTLY ONE uppercase label on its own line and nothing else:\n"
    "ORACLE_ARTIFACT  — A (the XML) has an error B gets right (a typo, a missing or extra "
    "space joining/splitting words, a dropped character); B is correct Finnish.\n"
    "READER_DEFECT    — B (the PDF text) has a garbled or misread word A gets right (a glyph "
    "swapped, a slash or stray mark inside a word, a non-word); A is correct Finnish.\n"
    "SEGMENTATION_NOISE — B contains EXTRA text that is not part of the provision body at all "
    "because the PDF extraction over-captured page furniture: a running header or page number "
    "(e.g. 'HE 58/1995 vp 12'), a signature block, or a trailing dashline / blank run "
    "(e.g. '— — —'). A is the clean body; the words that DO overlap agree. Choose this over "
    "ORACLE_ARTIFACT whenever A is fine and B merely has boundary/layout junk appended or "
    "inserted.\n"
    "GENUINE_DIFFERENCE — both A and B are valid Finnish but genuinely say different things "
    "(different words, numbers, or clauses).\n"
    "EQUIVALENT       — A and B mean exactly the same and differ only by punctuation/spacing/"
    "dashes with no change of words or numbers.\n"
    "UNCERTAIN        — you cannot tell."
)

_LABELS = {v.name: v for v in DivergenceVerdict}
#: Bounded snippet fed to the model — long bodies are trimmed around the FIRST divergence so
#: the model sees the actual disagreement, not a wall of identical prose. Flat quantifiers.
_MAX_SNIPPET = 600


def _snippet_around_first_diff(left: str, right: str, *, radius: int = _MAX_SNIPPET) -> "tuple[str, str]":
    """Trim both bodies to a window around their first divergence (bounded model input)."""
    i = 0
    while i < min(len(left), len(right)) and left[i] == right[i]:
        i += 1
    lo = max(0, i - radius // 4)
    return left[lo : lo + radius], right[lo : lo + radius]


def build_adjudication_prompt(left: str, right: str) -> "tuple[str, str]":
    """Return ``(system, user)`` for the divergence adjudication (pure, testable)."""
    a, b = _snippet_around_first_diff(left, right)
    user = f"A (XML oracle):\n{a}\n\nB (PDF bill text):\n{b}\n\nLabel:"
    return _ADJUDICATION_SYSTEM, user


_LABEL_RE = re.compile(
    r"\b(ORACLE_ARTIFACT|READER_DEFECT|SEGMENTATION_NOISE|GENUINE_DIFFERENCE|EQUIVALENT|UNCERTAIN)\b"
)


def parse_verdict(content: str) -> DivergenceVerdict:
    """Parse the model's reply to a verdict (pure); unrecognized → UNCERTAIN, never raises."""
    m = _LABEL_RE.search(content or "")
    return _LABELS[m.group(1)] if m is not None else DivergenceVerdict.UNCERTAIN


#: Code fingerprint of the FI-specific adjudication contract — the system prompt PLUS the
#: closed verdict-label set. The determinism-firewall cache folds this into its content-address
#: key (alongside the model id), so any change to the prompt or the label vocabulary MECHANICALLY
#: invalidates every stored verdict rather than serving a stale read from a superseded classifier.
def adjudication_prompt_fingerprint() -> str:
    """Short SHA-256 fingerprint of the adjudication prompt + label set (cache-key input)."""
    h = hashlib.sha256()
    h.update(_ADJUDICATION_SYSTEM.encode("utf-8"))
    h.update(b"\x00")
    h.update("|".join(v.value for v in DivergenceVerdict).encode("utf-8"))
    return h.hexdigest()[:16]


def adjudicate_payload_divergence(
    left: str, right: str, *, chat_fn: Callable[[str, str], str]
) -> DivergenceVerdict:
    """Adjudicate one XML-vs-PDF proposed-body divergence via the injected local-LLM chat.

    ``chat_fn(system, user) -> content`` is the transport (real use:
    ``LlmWorkflowAdjudicator``); this keeps the FI-specific prompt/typing pure and testable
    and the transport/cache at the injected boundary. Any transport error surfaces as the
    caller's concern — this function does not swallow it.
    """
    system, user = build_adjudication_prompt(left, right)
    return parse_verdict(chat_fn(system, user))
