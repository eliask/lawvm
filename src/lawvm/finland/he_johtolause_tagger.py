"""LLM johtolause span-tagger for HE proposed-effect extraction (phase 2).

The mechanical enacting-clause recognizer
(:func:`lawvm.tools.fi_he_ir_compare.extract_enacting_clause_spans`) faces a precision/recall
dilemma with NO clean optimum. It must bound the amendment-verb-head → "seuraavasti:" terminator
window, and:

  * a structural mega-amendment's johtolause runs ~13k chars (HE 157/2018 amending 320/2017
    lists 259 targets across osat/luvut before "seuraavasti:"), so a SMALL bound drops the whole
    bill — whole-bill op_missing is 82% of op_missing over the 8435-HE census;
  * a LARGE bound turns yksityiskohtaiset-perustelut prose ("… X §:ää muutetaan siten, että …
    seuraavasti") into false clauses — raising the bound to 50k recovered the mega-bills but
    exploded op_extra by ~11k across 824 HEs.

This module removes the char bound by making the GENUINE-vs-perustelut decision the LLM's job,
not a length. Mechanical stays minimal (the caller enumerates candidate heads = amendment verb +
statute citation — cheap, high-recall, false positives included); this module CLASSIFIES each
candidate window as :attr:`JohtolauseTag.JOHTOLAUSE` (a genuine enacting clause that lists the
amended provisions and closes with "seuraavasti:") vs :attr:`JohtolauseTag.PERUSTELU`
(justification prose that merely DISCUSSES a change). The caller extends confirmed johtolause
candidates UNBOUNDED to their terminator and hands them to the SAME deterministic clause grammar
(``he_branch_parser._parse_one_clause``); the resulting ops are still EXACT-compared against the
trusted XML. The LLM is a READER/witness that SEGMENTS — never the equivalence judge — so the
exactness invariant is untouched.

Determinism firewall: the FI-specific prompt + tag typing are pure and the LLM transport is
injected as a ``chat_fn`` (real use wires
:class:`lawvm.ingest.llm_backends.llm_adjudicator.LlmWorkflowAdjudicator`), so the classifier is
hermetically testable with a scripted fake. Every classification is content-addressed cached
(candidate window + model + prompt fingerprint) exactly like
:mod:`lawvm.finland.he_payload_verdict_store`: a re-run is a HIT, a model/prompt edit RE-KEYS, no
stale reads.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable, Optional


class JohtolauseTag(StrEnum):
    """The closed set of classifications for one candidate amendment-verb head."""

    JOHTOLAUSE = "johtolause"  # a genuine enacting clause (lists amended provisions, → "seuraavasti:")
    PERUSTELU = "perustelu"  # justification prose that merely discusses a change → NOT a directive
    UNCERTAIN = "uncertain"  # the model would not commit → keep as a mechanical fallback candidate

    @property
    def is_genuine(self) -> bool:
        """True iff this candidate is a genuine enacting clause to extract ops from."""
        return self is JohtolauseTag.JOHTOLAUSE


#: The FI-specific classification contract. Kept in Finnish (the source language) — the local
#: model reasons over Finnish statutory register better than a translated prompt. A genuine
#: johtolause ENUMERATES the amended provisions and ends in "seuraavasti:"; perustelut prose
#: EXPLAINS a change (often quoting the same verb + citation) but is not the directive itself.
_TAG_SYSTEM = (
    "Olet suomalaisen hallituksen esityksen (HE) rakenteen analysoija. Sinulle annetaan "
    "tekstikatkelma, joka alkaa muutosverbistä (muutetaan/lisätään/kumotaan/korvataan) ja "
    "säädösviittauksesta (N/VUOSI). Päätä, onko katkelma lakiehdotuksen VARSINAINEN JOHTOLAUSE "
    "vai PERUSTELUTEKSTIÄ. Vastaa TÄSMÄLLEEN yhdellä isoin kirjaimin kirjoitetulla sanalla "
    "omalla rivillään, äläkä muuta:\n"
    "JOHTOLAUSE — katkelma on lakiehdotuksen johdantokappale, joka LUETTELEE muutettavat pykälät "
    "(1 §, 2 §, …) ja päättyy sanaan \"seuraavasti:\". Se on itse säädösteksti.\n"
    "PERUSTELU  — katkelma on perustelutekstiä (esim. yksityiskohtaiset perustelut), joka "
    "SELITTÄÄ tai kuvaa muutosta (\"… ehdotetaan muutettavaksi siten, että …\") mutta ei ole "
    "varsinainen luetteleva johtolause.\n"
    "UNCERTAIN  — et pysty päättämään."
)

_LABELS = {v.name: v for v in JohtolauseTag}
_LABEL_RE = re.compile(r"\b(JOHTOLAUSE|PERUSTELU|UNCERTAIN)\b")

#: Bounded classification window fed to the model — enough of the candidate to see whether it
#: enumerates provisions toward a "seuraavasti:" (genuine) or reads as explanatory prose. The
#: full unbounded span is assembled by the CALLER only after a JOHTOLAUSE verdict, so the model
#: never has to ingest a 13k-char mega-johtolause to classify it.
_CLASSIFY_WINDOW = 500


def build_tag_prompt(window: str) -> "tuple[str, str]":
    """Return ``(system, user)`` for classifying one candidate head window (pure, testable)."""
    user = f"Katkelma:\n{(window or '')[:_CLASSIFY_WINDOW]}\n\nLuokka:"
    return _TAG_SYSTEM, user


def parse_tag(content: str) -> JohtolauseTag:
    """Parse the model's reply to a tag (pure); unrecognized → UNCERTAIN, never raises."""
    m = _LABEL_RE.search(content or "")
    return _LABELS[m.group(1)] if m is not None else JohtolauseTag.UNCERTAIN


def tag_prompt_fingerprint() -> str:
    """Short SHA-256 fingerprint of the tag prompt + label set (cache-key input).

    Folded into the firewall-cache key alongside the model id, so any edit to the prompt or the
    label vocabulary MECHANICALLY invalidates every stored tag rather than serving a stale read.
    """
    h = hashlib.sha256()
    h.update(_TAG_SYSTEM.encode("utf-8"))
    h.update(b"\x00")
    h.update("|".join(v.value for v in JohtolauseTag).encode("utf-8"))
    return h.hexdigest()[:16]


def classify_candidate(window: str, *, chat_fn: Callable[[str, str], str]) -> JohtolauseTag:
    """Classify one candidate amendment-verb head window via the injected local-LLM chat.

    ``chat_fn(system, user) -> content`` is the transport (real use: ``LlmWorkflowAdjudicator``);
    keeping the FI prompt/typing pure and the transport/cache at the injected boundary is what
    makes this hermetically testable. A transport error surfaces to the caller — not swallowed.
    """
    system, user = build_tag_prompt(window)
    return parse_tag(chat_fn(system, user))


# --------------------------------------------------------------------------- #
# Determinism-firewall cache (content-addressed johtolause tags).              #
# --------------------------------------------------------------------------- #

#: Default sibling derived-store path (mirrors the payload-verdict store).
FI_HE_JOHTOLAUSE_TAG_STORE = "data/fi_he_johtolause_tags.farchive"

#: Bump when the tag-row SHAPE or KEY construction changes (independently of the prompt
#: fingerprint the module owns) so a superseded row layout never shadows a fresh evaluation.
_CACHE_SCHEMA_VERSION = "johtolause_tag.v1"


def tagger_fingerprint(tagger_id: str) -> str:
    """Fold the model id + tag-prompt fingerprint into one cache-key component."""
    return f"{tagger_id}@{tag_prompt_fingerprint()}"


def tag_cache_key(window: str, *, tagger_id: str) -> str:
    """Content-address a tag by (schema, candidate window, tagger fingerprint).

    The window is length-prefixed then NUL-joined with the other parts so no two distinct inputs
    can collide on one digest. Pure — the SAME inputs always yield the SAME key.
    """
    fp = tagger_fingerprint(tagger_id)
    h = hashlib.sha256()
    for part in (_CACHE_SCHEMA_VERSION, window, fp):
        b = part.encode("utf-8")
        h.update(str(len(b)).encode("ascii"))
        h.update(b"\x00")
        h.update(b)
    return h.hexdigest()


def tag_locator(key: str) -> str:
    """Content-addressed store locator for a tag key (per-digest record)."""
    return f"he_johtolause_tag/{key}"


@dataclass(frozen=True, slots=True)
class CachedTag:
    """A cache lookup outcome: the typed tag plus whether it was served from the store."""

    tag: JohtolauseTag
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class TagRow:
    """The persisted, self-describing tag provenance record — the TYPED carrier crossing the
    store seam (a named record, never a bare ``dict[str, Any]``). Its field names are exactly the
    persisted JSON keys, so serialization is a mechanical :func:`dataclasses.asdict` round-trip."""

    tag: str
    is_genuine: bool
    tagger_id: str
    prompt_fingerprint: str
    schema_version: str
    window_sha256: str
    window_len: int
    created_at: str


class JohtolauseTagStore:
    """A farchive of content-addressed johtolause tags (the determinism-firewall cache)."""

    def __init__(self, path: str = FI_HE_JOHTOLAUSE_TAG_STORE) -> None:
        from farchive import Farchive

        self._fa = Farchive(path)
        self.path = path

    def get(self, key: str) -> Optional[TagRow]:
        """Read a persisted tag row by key (``None`` on miss)."""
        span = self._fa.resolve(tag_locator(key))
        if span is None:
            return None
        data = self._fa.read(span.digest)
        if data is None:
            return None
        return TagRow(**json.loads(data.decode("utf-8")))

    def put(self, key: str, row: TagRow) -> str:
        """Persist one tag row (deterministic sorted-keys JSON); returns the blob digest."""
        return self._fa.store(
            tag_locator(key),
            json.dumps(asdict(row), ensure_ascii=False, sort_keys=True).encode("utf-8"),
            storage_class="he_johtolause_tag",
            metadata={"tag": row.tag, "tagger_id": row.tagger_id},
        )

    def close(self) -> None:
        self._fa.close()


def _tag_row(tag: JohtolauseTag, window: str, *, tagger_id: str) -> TagRow:
    """Build the persisted tag row (self-describing provenance, no full window stored)."""
    return TagRow(
        tag=tag.value,
        is_genuine=tag.is_genuine,
        tagger_id=tagger_id,
        prompt_fingerprint=tag_prompt_fingerprint(),
        schema_version=_CACHE_SCHEMA_VERSION,
        window_sha256=hashlib.sha256(window.encode("utf-8")).hexdigest(),
        window_len=len(window),
        created_at=datetime.now(tz=timezone.utc).isoformat(),
    )


def classify_candidate_cached(
    window: str,
    *,
    chat_fn: Callable[[str, str], str],
    tagger_id: str,
    store: JohtolauseTagStore,
) -> CachedTag:
    """Cache-through classification: a HIT returns the stored tag without touching ``chat_fn``.

    On a MISS the pure classifier runs ONCE via the injected transport and the tag is persisted
    content-addressed; on a HIT the model is NOT invoked, so re-runs are free and the tag is
    stable across runs. The tag is a pure function of ``(window, tagger_id, prompt)`` — all three
    are folded into the key.
    """
    key = tag_cache_key(window, tagger_id=tagger_id)
    cached = store.get(key)
    if cached is not None:
        return CachedTag(tag=JohtolauseTag(cached.tag), cache_hit=True)
    tag = classify_candidate(window, chat_fn=chat_fn)
    store.put(key, _tag_row(tag, window, tagger_id=tagger_id))
    return CachedTag(tag=tag, cache_hit=False)
