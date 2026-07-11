"""Recovered-text store — content-addressed vision transcriptions (determinism firewall).

The vision-witness glyph-substitution repair (:func:`lawvm.ingest.text_layer_repair.
reconcile_vision_tokens`) needs an INDEPENDENT read of a rendered PDF page. That read
comes from a vision model, which is NON-deterministic — but the headline ``exact`` count
of the PDF→IR equivalence eval MUST stay byte-reproducible. This store is the firewall
that reconciles those: a vision page transcription is persisted CONTENT-ADDRESSED by
``(artifact digest, page index, prompt/model fingerprint)``, exactly the discipline the
derived-IR store (:mod:`lawvm.ingest.parsed_store`) and the FI johtolause tag store use.

Two consequences make the free lane's integrity non-negotiable:

  * A WARM store replays a transcription deterministically (a byte-identical HIT keyed by
    the immutable inputs); a model or prompt swap re-keys (a new
    :func:`~lawvm.ingest.llm_backends.prompt_fingerprint.prompt_fingerprint`) so a stale
    read is never served under a superseded contract.
  * A COLD lookup returns ``None``. The caller's REPLAY-mode reader turns that into an
    empty read (no backend call, the deterministic offline sweep stays byte-identical);
    only an explicit LIVE-mode reader calls the model on a miss and persists the result.

The store is jurisdiction-neutral: it addresses a page of any PDF by its artifact digest.
The FI HE comparison wires it (render + backend + page location) in ``fi_he_ir_compare``.
"""
from __future__ import annotations

from typing import Optional

#: Default recovered-text store path (a farchive, sibling to the derived-IR store). FI
#: callers may pass their own path; kept generic so the store is not FI-anchored.
RECOVERED_TEXT_STORE_DEFAULT = "data/fi_recovered_text.farchive"


def recovered_text_locator(artifact_digest: str, page_index: int, fingerprint: str) -> str:
    """Content-addressed key for one page's vision transcription.

    ``recovered/<artifact_digest>/<fingerprint>/page/<NNNN>`` — the page index of the
    SOURCE artifact (so two callers reading the same page collide on one entry), under a
    prompt/model FINGERPRINT prefix (so a prompt or model swap writes a NEW keyed record
    without overwriting the old — versioned + auditable, exactly like the parsed-IR store).
    """
    return f"recovered/{artifact_digest}/{fingerprint}/page/{page_index:04d}"


class RecoveredTextStore:
    """A farchive of vision page transcriptions, content-addressed by source × page × fingerprint."""

    def __init__(self, path: str = RECOVERED_TEXT_STORE_DEFAULT) -> None:
        from farchive import Farchive

        self._fa = Farchive(path)
        self.path = path

    def get(self, artifact_digest: str, page_index: int, fingerprint: str) -> Optional[str]:
        """The stored transcription for ``(digest, page, fingerprint)``, or ``None`` (cold)."""
        span = self._fa.resolve(
            recovered_text_locator(artifact_digest, page_index, fingerprint)
        )
        if span is None:
            return None
        data = self._fa.read(span.digest)
        if data is None:
            return None
        return data.decode("utf-8")

    def put(
        self, artifact_digest: str, page_index: int, fingerprint: str, text: str
    ) -> str:
        """Persist one page's vision transcription content-addressed (returns the blob digest)."""
        return self._fa.store(
            recovered_text_locator(artifact_digest, page_index, fingerprint),
            text.encode("utf-8"),
            storage_class="recovered_text",
            metadata={
                "source_digest": artifact_digest,
                "page_index": str(page_index),
                "fingerprint": fingerprint,
            },
        )

    def close(self) -> None:
        self._fa.close()
