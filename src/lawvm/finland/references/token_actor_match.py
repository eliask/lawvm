"""Shared TOKEN-NATIVE actor-phrase matcher for the FI surface frame lenses.

The H4 actor/modal lens and the H5 delegation lens both match the SAME actor
vocabulary — the institutional :data:`lawvm.finland.canonical_actor_registry.REGISTRY`
phrases UNION a closed generic role-actor list — and both run ``REGISTRY.lookup``
for ambiguity. Historically each carried its own regex ``_ACTOR_RE`` alternation
over raw text. This module is the single, shared TOKEN-sequence matcher both
recognizers consume (rule-of-three substrate): there is exactly ONE token-actor
matcher, not two divergent ones.

WHY TOKEN-NATIVE
================
A registry actor phrase is multi-word and may be hyphenated, e.g.
``liikenne- ja viestintäministeriö`` or
``sosiaali- ja terveysalan lupa- ja valvontavirasto``. The Finnish tokenizer
(:mod:`lawvm.finland.legal_surface.tokenize`) is LOSSLESS: concatenating the
``.text`` of consecutive tokens reproduces the source substring exactly,
including the single-space / hyphen separators that appear verbatim inside those
phrases. So a phrase matches a run of consecutive tokens iff the concatenation of
their verbatim ``Token.text`` equals the phrase. Token boundaries give the
word-boundary guarantee the old regex spelled out with lookarounds: ``kunta``
cannot match inside ``kuntalainen`` because that is a single ``word`` token.

CASE SENSITIVITY (NORMATIVE)
============================
The registry is case-sensitive ("VM" is the ministry; "vm" is not registered),
so matching uses the VERBATIM, case-preserving ``Token.text`` — NOT
``Token.normalized``. Sentence-initial capitalized role variants are part of the
phrase set (built by the lenses via ``expand_role_actor_phrases``).

MATCHING DISCIPLINE
===================
- Longest-phrase-first at each start: the matcher prefers the phrase spanning the
  most tokens, mirroring the old longest-first alternation (so
  ``valvontaviranomainen`` beats ``viranomainen`` and the multi-word ministry name
  beats a bare ``ministeriö``).
- A match starts on a non-whitespace token and ends on a non-whitespace token; the
  emitted span comes from token ``.char_start`` / ``.char_end`` (whole-token
  aligned). Spans are therefore RE-BASELINED relative to the old char-regex spans
  (token-aligned), which is expected and accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from lawvm.core.legal_surface_tokens import Token

_NONSPACE = "nonspace"


@dataclass(frozen=True, slots=True)
class ActorMatch:
    """A token-aligned actor-phrase match.

    Attributes:
        surface:      The verbatim matched surface (== ``phrase``).
        start_index:  Index of the first token of the match in the tape.
        end_index:    Index PAST the last token of the match (exclusive).
        char_start:   ``Token.char_start`` of the first token (source offset).
        char_end:     ``Token.char_end`` of the last token (source offset).
    """

    surface: str
    start_index: int
    end_index: int
    char_start: int
    char_end: int


def _max_phrase_token_len(phrases: Sequence[str]) -> int:
    """Upper bound on how many tokens any phrase can span.

    A phrase's token count is bounded by its character length (each token is at
    least one char), but we want a tight bound to cap the inner walk. A phrase
    like ``sosiaali- ja terveysalan lupa- ja valvontavirasto`` spans words, dashes
    and single-space whitespace tokens; the worst case is roughly one token per
    run of like characters. We bound by counting maximal runs of word/dash/space.
    """
    best = 1
    for phrase in phrases:
        runs = 1
        for prev, cur in zip(phrase, phrase[1:], strict=False):
            # a transition between a space and a non-space (or vice versa), or
            # between a dash and a non-dash, starts a new token.
            prev_space = prev.isspace()
            cur_space = cur.isspace()
            prev_dash = prev in "-‐‑‒–—―"
            cur_dash = cur in "-‐‑‒–—―"
            if prev_space != cur_space or prev_dash or cur_dash:
                runs += 1
        best = max(best, runs)
    return best


class TokenActorMatcher:
    """A reusable longest-first token-actor matcher over a fixed phrase set.

    Construct once per phrase set (module scope in the consuming recognizer); call
    :meth:`find_all` / :meth:`find_in_window` per tape. The phrase set is the
    consumer's union of registry phrases and closed role actors — this matcher is
    vocabulary-agnostic and does NOT itself read the registry.
    """

    def __init__(self, phrases: Sequence[str]) -> None:
        self._phrase_set = frozenset(phrases)
        self._max_token_span = _max_phrase_token_len(phrases)

    def _match_at(
        self, tokens: Tuple[Token, ...], i: int, hi: int
    ) -> Tuple[int, str] | None:
        """Longest phrase matching tokens starting at index ``i`` within [i, hi).

        Returns (end_index_exclusive, surface) for the longest phrase whose
        verbatim token-text concatenation is in the phrase set, or None.
        """
        n = min(hi, i + self._max_token_span)
        acc = ""
        best: Tuple[int, str] | None = None
        for j in range(i, n):
            acc += tokens[j].text
            if acc in self._phrase_set:
                best = (j + 1, acc)  # keep extending to prefer the longest
        return best

    def find_in_window(
        self, tokens: Tuple[Token, ...], lo: int, hi: int
    ) -> list[ActorMatch]:
        """Non-overlapping longest-first actor matches over tokens[lo:hi).

        Matches only start on a non-whitespace token. After a match, scanning
        resumes at the token PAST the match (no overlap), mirroring ``re.finditer``
        over a non-overlapping alternation.
        """
        out: list[ActorMatch] = []
        i = lo
        while i < hi:
            tok = tokens[i]
            if tok.category == "whitespace":
                i += 1
                continue
            m = self._match_at(tokens, i, hi)
            if m is None:
                i += 1
                continue
            end_index, surface = m
            out.append(
                ActorMatch(
                    surface=surface,
                    start_index=i,
                    end_index=end_index,
                    char_start=tokens[i].char_start,
                    char_end=tokens[end_index - 1].char_end,
                )
            )
            i = end_index
        return out

    def find_all(self, tokens: Tuple[Token, ...]) -> list[ActorMatch]:
        """All non-overlapping actor matches over the whole token sequence."""
        return self.find_in_window(tokens, 0, len(tokens))
