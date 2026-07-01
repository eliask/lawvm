"""Regression: a momentti REPLACE with a bled johtolause chapter scope must
still bind a chapter-less (root-level) target section (#154).

Amendment 2009/1304 amends Laki ajopiirturikorttien myöntämisen järjestämisestä
(2004/629). Its johtolause lists ``... 2 luvun otsikko ... 16 ja 17 § sekä 18 §:n
3 momentti seuraavasti``, so the ``2 luku`` scope introduced by ``2 luvun otsikko``
bleeds forward onto the later bare ``18 §`` citation. The compiled op is therefore
``REPLACE 2 luku 18 § 3 mom``.

In the base statute §18 (Voimaantulo) is a ROOT-LEVEL section — it sits after the
``3 luku`` close, under no chapter at all. The whole-section moves for §16/§17
(also bled to ``2 luku`` but living in ``3 luku``) were rescued by the section
move+replace rebind, but the subsection-scoped §18 3 mom REPLACE was dropped:
the carried-chapter unique-global fallback refused to bind a chapter-scoped op onto
a chapter-less (``global_chapter is None``) section. The op silently vanished and the
momentti kept its stale agency name (``Ajoneuvohallintokeskuksen``) instead of the
2009/1304 rename to ``Liikenteen turvallisuusviraston``.

The fix generalizes the descendant-target in-place bind (previously INSERT-only) to
all descendant edits: a subsection/momentti REPLACE never moves its section, so
binding the globally-unique section in place — including at root level — is correct.

This test replays the real corpus across the 2009/1304 boundary and asserts §18's
3rd momentti now carries the amended agency name, matching the oracle. Skipped when
the corpus archive is absent.
"""

from __future__ import annotations

import pytest


def _corpus_available() -> bool:
    from lawvm.corpus_store import resolve_farchive_path

    try:
        path, _rule = resolve_farchive_path("finlex.farchive")
    except Exception:
        return False
    return path is not None and path.exists() and path.stat().st_size > 0


_corpus_skip = pytest.mark.skipif(
    not _corpus_available(),
    reason="finlex corpus archive not resolvable; skipping real-corpus chapter-bleed replay test",
)

_OLD_AGENCY = "Ajoneuvohallintokeskuksen palvelusopimukseen"
_NEW_AGENCY = "Liikenteen turvallisuusviraston palvelusopimukseen"


def _bundle():
    from lawvm.tools.trace_section import build_trace_bundle

    return build_trace_bundle("2004/629", "2009/1304", "18 §", mode="legal_pit")


@_corpus_skip
def test_2004_629_section_18_momentti_replace_binds_root_level_section() -> None:
    bundle = _bundle()

    # The amendment does change §18: before it carries the old agency name.
    assert _OLD_AGENCY in bundle["before_text"]
    assert bundle["changed"] is True

    # After 2009/1304 the momentti carries the renamed agency — the REPLACE
    # resolved onto the chapter-less §18 instead of being dropped.
    assert _NEW_AGENCY in bundle["after_text"]
    assert _OLD_AGENCY not in bundle["after_text"]


@_corpus_skip
def test_2004_629_section_18_matches_oracle_after_amendment() -> None:
    bundle = _bundle()
    # The rescued momentti makes the post-amendment §18 identical to the oracle.
    assert bundle["after_vs_oracle"] == pytest.approx(1.0)
    assert _NEW_AGENCY in bundle["oracle_text"]
