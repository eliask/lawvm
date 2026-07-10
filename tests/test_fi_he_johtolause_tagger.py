"""Hermetic tests for the LLM johtolause span-tagger + its determinism-firewall cache.

The LLM transport is INJECTED (a scripted / counting fake ``chat_fn`` or ``classify_fn``) and
the store is a tmp-path farchive, so these prove the whole cache-through + LLM-gated extraction
path with NO backend: perustelut candidates are rejected, a genuine mega-johtolause whose
terminator is far past any mechanical char bound is captured UNBOUNDED, a re-classification is a
cache HIT that invokes the model ZERO more times, and any change to the window / model / prompt
re-keys.
"""
from __future__ import annotations

from lawvm.finland.he_johtolause_tagger import (
    JohtolauseTag,
    JohtolauseTagStore,
    classify_candidate,
    classify_candidate_cached,
    parse_tag,
    tag_cache_key,
    tag_prompt_fingerprint,
)
from lawvm.tools.fi_he_ir_compare import extract_enacting_clause_spans_llm


class _CountingChat:
    """A scripted chat_fn recording how many times the model was actually invoked."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


def test_parse_tag_maps_labels_and_defaults_uncertain() -> None:
    assert parse_tag("JOHTOLAUSE") is JohtolauseTag.JOHTOLAUSE
    assert parse_tag("  PERUSTELU\n") is JohtolauseTag.PERUSTELU
    assert parse_tag("the model rambled with no label") is JohtolauseTag.UNCERTAIN
    assert parse_tag("") is JohtolauseTag.UNCERTAIN


def test_classify_candidate_is_pure_over_injected_chat() -> None:
    chat = _CountingChat("JOHTOLAUSE")
    assert classify_candidate("muutetaan lain (320/2017) 1 § ... seuraavasti:", chat_fn=chat) is (
        JohtolauseTag.JOHTOLAUSE
    )
    assert chat.calls == 1


def test_is_genuine_only_for_johtolause() -> None:
    assert JohtolauseTag.JOHTOLAUSE.is_genuine is True
    assert JohtolauseTag.PERUSTELU.is_genuine is False
    assert JohtolauseTag.UNCERTAIN.is_genuine is False


def _store(tmp_path) -> JohtolauseTagStore:
    return JohtolauseTagStore(str(tmp_path / "tags.farchive"))


def test_second_classification_is_a_cache_hit(tmp_path) -> None:
    chat = _CountingChat("PERUSTELU")
    store = _store(tmp_path)
    w = "muutetaan pykälän numero (entinen II osan 1 luvun 1 §). Taksiliikenne ..."
    first = classify_candidate_cached(w, chat_fn=chat, tagger_id="llm:qwen", store=store)
    assert first.tag is JohtolauseTag.PERUSTELU and first.cache_hit is False and chat.calls == 1
    second = classify_candidate_cached(w, chat_fn=chat, tagger_id="llm:qwen", store=store)
    assert second.tag is JohtolauseTag.PERUSTELU and second.cache_hit is True
    assert chat.calls == 1  # served from the firewall cache; model NOT re-invoked
    store.close()


def test_cache_survives_reopen_and_ignores_model_reflip(tmp_path) -> None:
    flip = _CountingChat("JOHTOLAUSE")
    store1 = _store(tmp_path)
    w = "muutetaan lain (12/2020) 5 § seuraavasti:"
    classify_candidate_cached(w, chat_fn=flip, tagger_id="m", store=store1)
    store1.close()
    # a model that now flips to a different label must NOT change the stored tag
    flip.reply = "PERUSTELU"
    store2 = JohtolauseTagStore(str(tmp_path / "tags.farchive"))
    out = classify_candidate_cached(w, chat_fn=flip, tagger_id="m", store=store2)
    assert out.cache_hit is True and out.tag is JohtolauseTag.JOHTOLAUSE and flip.calls == 1
    store2.close()


def test_key_folds_window_and_model_and_prompt() -> None:
    a = tag_cache_key("w1", tagger_id="m1")
    assert a != tag_cache_key("w2", tagger_id="m1")  # window matters
    assert a != tag_cache_key("w1", tagger_id="m2")  # model id matters
    assert a == tag_cache_key("w1", tagger_id="m1")  # pure
    fp = tag_prompt_fingerprint()
    assert fp == tag_prompt_fingerprint() and len(fp) == 16


# --------------------------------------------------------------------------- #
# LLM-gated extraction: perustelut rejected, mega-johtolause captured unbounded #
# --------------------------------------------------------------------------- #

_SEC = "§"


def test_llm_gate_rejects_perustelu_keeps_johtolause() -> None:
    # Two candidate heads: a genuine johtolause and a perustelut sentence. Both carry
    # verb+citation+§+"seuraavasti:", so the MECHANICAL signature cannot separate them; the
    # injected classifier tags by leading text and only the genuine one survives.
    genuine = "muutetaan aitolain (123/2020) 5 " + _SEC + " seuraavasti: uusi 5 §."
    perustelu = (
        "Pykalaa ehdotetaan muutettavaksi siten (999/1999) etta 3 " + _SEC
        + " tarkistetaan seuraavasti: perustelu jatkuu."
    )

    def classify(window: str):
        return (
            JohtolauseTag.JOHTOLAUSE if window.startswith("muutetaan aitolain") else JohtolauseTag.PERUSTELU
        )

    spans = extract_enacting_clause_spans_llm(
        "Lakiehdotukset " + perustelu + " " + genuine, classify_fn=classify
    )
    assert len(spans) == 1
    assert "(123/2020)" in spans[0] and "(999/1999)" not in spans[0]


def test_llm_gate_captures_mega_johtolause_unbounded() -> None:
    # A structural mega-amendment: the provision list pushes "seuraavasti:" ~4k chars past the
    # head — far beyond the mechanical bound that drops the whole bill. Under the LLM lane a
    # JOHTOLAUSE verdict extends the span UNBOUNDED to its own terminator, list intact.
    long_list = "".join(f"{n} {_SEC}, " for n in range(1, 400))  # ~3.6k chars
    clause = "muutetaan isolain (320/2017) " + long_list + "seuraavasti:"
    spans = extract_enacting_clause_spans_llm(
        "Lakiehdotukset 1. Laki isolain muuttamisesta " + clause,
        classify_fn=lambda _w: JohtolauseTag.JOHTOLAUSE,
    )
    assert len(spans) == 1
    assert spans[0].rstrip().endswith("seuraavasti:")
    assert "399 §" in spans[0]  # the tail of the long list survived — no char truncation


def test_llm_gate_uncertain_is_not_kept() -> None:
    # An UNCERTAIN verdict is not a genuine clause; the candidate is dropped (a mechanical
    # fallback could reconsider it, but the LLM lane itself does not emit ops for it).
    clause = "muutetaan lain (1/2020) 5 " + _SEC + " seuraavasti: x."
    spans = extract_enacting_clause_spans_llm(
        "Lakiehdotukset " + clause, classify_fn=lambda _w: JohtolauseTag.UNCERTAIN
    )
    assert spans == []
