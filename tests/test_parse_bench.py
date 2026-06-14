"""Tests for the parse-bench grammar-coverage tool (corpus-independent core)."""

from __future__ import annotations

from lawvm.tools.parse_bench import _AMENDMENT_VERB_PREFIXES


def test_amendment_verb_prefixes_classify_real_johtolause_heads() -> None:
    """The amendment-vs-enactment split keys on the leading operative verb."""

    def is_amendment(head: str) -> bool:
        return " ".join(head.split())[:24].lower().startswith(_AMENDMENT_VERB_PREFIXES)

    # Amendment johtolauses (start with an operative amendment verb).
    assert is_amendment("muutetaan lain 5 §")
    assert is_amendment("kumotaan 7 §")
    assert is_amendment("Lisätään lakiin uusi 9 §")
    assert is_amendment("siirretään 3 § 4 lukuun")
    assert is_amendment("korvataan taulukko")

    # Non-amendment enactments (originally-enacted statutes / decrees).
    assert not is_amendment("Valtiovarainministeriön päätöksen mukaisesti säädetään")
    assert not is_amendment("Verohallinto on verotusmenettelystä annetun lain")
    assert not is_amendment("Eduskunnan päätöksen mukaisesti säädetään")
