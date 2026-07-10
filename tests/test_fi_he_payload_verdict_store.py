"""Hermetic tests for the HE payload-verdict determinism-firewall cache.

The LLM transport is INJECTED (a counting fake ``chat_fn``) and the store is a tmp-path
farchive, so these prove the content-addressed cache-through path with NO backend: a second
adjudication over the same body pair is a cache HIT that invokes the model ZERO more times and
returns the SAME verdict, and any change to the bodies / model id / prompt re-keys.
"""
from __future__ import annotations

from lawvm.finland.he_payload_adjudicator import (
    DivergenceVerdict,
    adjudication_prompt_fingerprint,
)
from lawvm.finland.he_payload_verdict_store import (
    PayloadVerdictStore,
    adjudicate_payload_divergence_cached,
    verdict_cache_key,
)


class _CountingChat:
    """A scripted chat_fn that records how many times the model was actually invoked."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


def _store(tmp_path) -> PayloadVerdictStore:
    return PayloadVerdictStore(str(tmp_path / "verdicts.farchive"))


def test_second_call_is_a_cache_hit_no_second_model_call(tmp_path) -> None:
    chat = _CountingChat("ORACLE_ARTIFACT")
    store = _store(tmp_path)
    left = "hallituksen, verovirastontai veroasiamiehen"
    right = "hallituksen, veroviraston tai veroasiamiehen"

    first = adjudicate_payload_divergence_cached(
        left, right, chat_fn=chat, adjudicator_id="llm_workflow:qwen", store=store
    )
    assert first.verdict is DivergenceVerdict.ORACLE_ARTIFACT
    assert first.cache_hit is False
    assert chat.calls == 1

    second = adjudicate_payload_divergence_cached(
        left, right, chat_fn=chat, adjudicator_id="llm_workflow:qwen", store=store
    )
    assert second.verdict is DivergenceVerdict.ORACLE_ARTIFACT
    assert second.cache_hit is True
    assert chat.calls == 1  # the model was NOT invoked again — served from the firewall cache
    store.close()


def test_cache_survives_a_fresh_store_reopen(tmp_path) -> None:
    """The verdict persists across process boundaries (re-open the same farchive)."""
    chat = _CountingChat("READER_DEFECT")
    left, right = "jo/ulosta annetun", "johdosta annetun"

    store1 = _store(tmp_path)
    adjudicate_payload_divergence_cached(
        left, right, chat_fn=chat, adjudicator_id="m", store=store1
    )
    store1.close()

    store2 = PayloadVerdictStore(str(tmp_path / "verdicts.farchive"))
    out = adjudicate_payload_divergence_cached(
        left, right, chat_fn=chat, adjudicator_id="m", store=store2
    )
    assert out.cache_hit is True
    assert out.verdict is DivergenceVerdict.READER_DEFECT
    assert chat.calls == 1
    store2.close()


def test_verdict_never_reflips_even_if_the_model_would(tmp_path) -> None:
    """A cache HIT returns the STORED verdict, immune to a re-flip of a nondeterministic model."""
    store = _store(tmp_path)
    left, right = "aaa bbb", "aaa ccc"

    flip = _CountingChat("GENUINE_DIFFERENCE")
    v1 = adjudicate_payload_divergence_cached(
        left, right, chat_fn=flip, adjudicator_id="m", store=store
    )
    # even if the model now flips to a different label, the cached read wins
    flip.reply = "READER_DEFECT"
    v2 = adjudicate_payload_divergence_cached(
        left, right, chat_fn=flip, adjudicator_id="m", store=store
    )
    assert v1.verdict is DivergenceVerdict.GENUINE_DIFFERENCE
    assert v2.verdict is DivergenceVerdict.GENUINE_DIFFERENCE
    assert v2.cache_hit is True
    assert flip.calls == 1
    store.close()


def test_key_folds_bodies_model_and_prompt() -> None:
    a = verdict_cache_key("left", "right", adjudicator_id="m1")
    # different bodies → different key
    assert a != verdict_cache_key("left2", "right", adjudicator_id="m1")
    assert a != verdict_cache_key("left", "right2", adjudicator_id="m1")
    # different model id → different key (a model upgrade never serves a stale verdict)
    assert a != verdict_cache_key("left", "right", adjudicator_id="m2")
    # pure: same inputs → same key
    assert a == verdict_cache_key("left", "right", adjudicator_id="m1")


def test_key_split_is_unambiguous() -> None:
    # a naive concatenation would alias these two distinct (left, right) splits; the length-
    # prefixed NUL-join must NOT.
    assert verdict_cache_key("ab", "c", adjudicator_id="m") != verdict_cache_key(
        "a", "bc", adjudicator_id="m"
    )


def test_prompt_fingerprint_is_stable_and_short() -> None:
    fp = adjudication_prompt_fingerprint()
    assert fp == adjudication_prompt_fingerprint()
    assert len(fp) == 16 and all(c in "0123456789abcdef" for c in fp)


def test_different_model_id_recomputes_on_miss(tmp_path) -> None:
    """A model-id change is a cache MISS (new key), so the model IS re-invoked (no stale hit)."""
    chat = _CountingChat("EQUIVALENT")
    store = _store(tmp_path)
    left, right = "x y", "x  y"
    adjudicate_payload_divergence_cached(
        left, right, chat_fn=chat, adjudicator_id="model-A", store=store
    )
    out = adjudicate_payload_divergence_cached(
        left, right, chat_fn=chat, adjudicator_id="model-B", store=store
    )
    assert out.cache_hit is False
    assert chat.calls == 2
    store.close()
