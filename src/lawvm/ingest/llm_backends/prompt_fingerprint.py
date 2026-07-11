"""Jurisdiction-neutral prompt/vocabulary fingerprint for determinism-firewall cache keys.

A content-addressed LLM cache MUST re-key when the PROMPT or the closed output
VOCABULARY changes — otherwise a warm store serves a STALE read computed under a
superseded contract (the exact defect this helper closes across every PDF→IR path).

This is the ONE canonical composition of that fingerprint. It was extracted from
three byte-identical FI copies (``he_johtolause_tagger.tag_prompt_fingerprint`` /
``he_payload_adjudicator.adjudication_prompt_fingerprint``) so every jurisdiction
AND the neutral vision/adjudication ingest lanes share it. Pure — no I/O, no live
backend; the SAME inputs always yield the SAME digest.
"""

from __future__ import annotations

import hashlib
from typing import Iterable


def prompt_fingerprint(*prompts: str, vocab: Iterable[str] = ()) -> str:
    """Short SHA-256 fingerprint over one-or-more system prompts + a closed vocabulary.

    Composition (matches the original FI shape byte-for-byte for a single prompt +
    a vocabulary): each prompt is UTF-8 encoded and NUL-terminated in order, then
    the pipe-joined vocabulary is appended. Truncated to 16 hex chars.

    DETERMINISTIC: identical ``(prompts, vocab)`` → identical digest across runs.
    SENSITIVE: any edit to any prompt, the prompt order, or the vocabulary changes
    the digest, so folding it into a cache key MECHANICALLY invalidates every stored
    row computed under the old contract.
    """
    h = hashlib.sha256()
    for p in prompts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    h.update("|".join(vocab).encode("utf-8"))
    return h.hexdigest()[:16]
