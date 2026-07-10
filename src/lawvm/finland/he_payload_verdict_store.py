"""Determinism-firewall cache for HE payload-divergence verdicts (content-addressed).

The T1 adjudicator (:mod:`lawvm.finland.he_payload_adjudicator`) is a PURE function of
``(left_body, right_body, model, prompt)`` — but the LLM transport that realizes it is neither
free nor byte-deterministic across runs. Per the ``parsed_store`` discipline (an LLM-derived
result may enter downstream consumers ONLY as a content-addressed record carrying the producing
model/pipeline id), this module is that record cache for the adjudication verdict:

  * The verdict is keyed by ``SHA-256(schema-version, left_body, right_body, adjudicator
    fingerprint)`` where the *adjudicator fingerprint* folds BOTH the model id and the
    adjudication-prompt fingerprint (:func:`~lawvm.finland.he_payload_adjudicator.
    adjudication_prompt_fingerprint`). A re-run over the same two bodies under the same model +
    prompt is a cache HIT — the verdict never re-flips. A model UPGRADE or a prompt edit changes
    the key and writes a NEW record without overwriting the old (versioned, auditable, no stale
    read).
  * The store is a farchive (sibling to ``data/fi_parsed_ir.farchive``) exactly like the derived
    IR store; the verdict row is deterministic sorted-keys JSON.

The transport (``chat_fn``) stays injected at the boundary so the whole cache-through path is
hermetically testable with a scripted fake and a tmp-path store: a second call over the same pair
is a HIT that runs the model ZERO more times.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from lawvm.finland.he_payload_adjudicator import (
    DivergenceVerdict,
    adjudication_prompt_fingerprint,
    adjudicate_payload_divergence,
)

#: Default sibling derived-store path (mirrors ``FI_PARSED_STORE`` = data/fi_parsed_ir.farchive).
FI_HE_PAYLOAD_VERDICT_STORE = "data/fi_he_payload_verdicts.farchive"

#: Bump when the verdict-row SHAPE or the KEY construction changes (independently of the prompt
#: fingerprint, which the adjudicator module owns) so a superseded row layout never shadows a
#: fresh evaluation.
_CACHE_SCHEMA_VERSION = "verdict.v1"


def adjudicator_fingerprint(adjudicator_id: str) -> str:
    """Fold the model id + adjudication-prompt fingerprint into one cache-key component."""
    return f"{adjudicator_id}@{adjudication_prompt_fingerprint()}"


def verdict_cache_key(left: str, right: str, *, adjudicator_id: str) -> str:
    """Content-address a divergence verdict by (schema, left, right, adjudicator fingerprint).

    The two bodies are length-prefixed then NUL-joined so no pair of distinct ``(left, right)``
    splits can collide on one digest (a plain concatenation would alias e.g. ``("ab","c")`` with
    ``("a","bc")``). Pure — the SAME inputs always yield the SAME key.
    """
    fp = adjudicator_fingerprint(adjudicator_id)
    h = hashlib.sha256()
    for part in (_CACHE_SCHEMA_VERSION, left, right, fp):
        b = part.encode("utf-8")
        h.update(str(len(b)).encode("ascii"))
        h.update(b"\x00")
        h.update(b)
    return h.hexdigest()


def verdict_locator(key: str) -> str:
    """Content-addressed store locator for a verdict key (per-digest record)."""
    return f"he_payload_verdict/{key}"


@dataclass(frozen=True, slots=True)
class CachedVerdict:
    """A cache lookup outcome: the typed verdict plus whether it was served from the store."""

    verdict: DivergenceVerdict
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class VerdictRow:
    """The persisted, self-describing verdict provenance record — the TYPED carrier crossing the
    store seam (a named record, never a bare ``dict[str, Any]``). Its field names are exactly the
    persisted JSON keys, so serialization is a mechanical :func:`dataclasses.asdict` round-trip."""

    verdict: str
    is_witness_disagreement: bool
    adjudicator_id: str
    prompt_fingerprint: str
    schema_version: str
    left_sha256: str
    right_sha256: str
    left_len: int
    right_len: int
    created_at: str


class PayloadVerdictStore:
    """A farchive of content-addressed HE payload-divergence verdicts (the firewall cache)."""

    def __init__(self, path: str = FI_HE_PAYLOAD_VERDICT_STORE) -> None:
        from farchive import Farchive

        self._fa = Farchive(path)
        self.path = path

    def get(self, key: str) -> Optional[VerdictRow]:
        """Read a persisted verdict row by key (``None`` on miss)."""
        span = self._fa.resolve(verdict_locator(key))
        if span is None:
            return None
        data = self._fa.read(span.digest)
        if data is None:
            return None
        return VerdictRow(**json.loads(data.decode("utf-8")))

    def put(self, key: str, row: VerdictRow) -> str:
        """Persist one verdict row (deterministic sorted-keys JSON); returns the blob digest."""
        return self._fa.store(
            verdict_locator(key),
            json.dumps(asdict(row), ensure_ascii=False, sort_keys=True).encode("utf-8"),
            storage_class="he_payload_verdict",
            metadata={
                "verdict": row.verdict,
                "adjudicator_id": row.adjudicator_id,
            },
        )

    def close(self) -> None:
        self._fa.close()


def _verdict_row(
    verdict: DivergenceVerdict, left: str, right: str, *, adjudicator_id: str
) -> VerdictRow:
    """Build the persisted verdict row (self-describing provenance, no bodies stored)."""
    return VerdictRow(
        verdict=verdict.value,
        is_witness_disagreement=verdict.is_witness_disagreement,
        adjudicator_id=adjudicator_id,
        prompt_fingerprint=adjudication_prompt_fingerprint(),
        schema_version=_CACHE_SCHEMA_VERSION,
        left_sha256=hashlib.sha256(left.encode("utf-8")).hexdigest(),
        right_sha256=hashlib.sha256(right.encode("utf-8")).hexdigest(),
        left_len=len(left),
        right_len=len(right),
        created_at=datetime.now(tz=timezone.utc).isoformat(),
    )


def adjudicate_payload_divergence_cached(
    left: str,
    right: str,
    *,
    chat_fn: Callable[[str, str], str],
    adjudicator_id: str,
    store: PayloadVerdictStore,
) -> CachedVerdict:
    """Cache-through T1 adjudication: HIT returns the stored verdict without touching ``chat_fn``.

    On a MISS the pure adjudicator (:func:`adjudicate_payload_divergence`) is run ONCE via the
    injected transport and the verdict is persisted content-addressed; on a HIT the model is NOT
    invoked at all, so re-runs are free and the verdict is stable across runs. The verdict is a
    pure function of ``(left, right, adjudicator_id, prompt)`` — all four are folded into the key.
    """
    key = verdict_cache_key(left, right, adjudicator_id=adjudicator_id)
    cached = store.get(key)
    if cached is not None:
        return CachedVerdict(verdict=DivergenceVerdict(cached.verdict), cache_hit=True)
    verdict = adjudicate_payload_divergence(left, right, chat_fn=chat_fn)
    store.put(key, _verdict_row(verdict, left, right, adjudicator_id=adjudicator_id))
    return CachedVerdict(verdict=verdict, cache_hit=False)
