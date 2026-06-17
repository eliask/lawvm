"""Token-tape migration status for the four frame lenses.

SUPERSEDED 2026-06-17 (decision B): the four frame lenses — actor_modal,
delegation, procedure, sanction — were REWRITTEN as token/grammar recognizers
over the TokenTape (retiring the regex-over-raw-text recognizers), deliberately
accepting token-aligned, re-baselined spans. The prior "DEFERRED on raw_text"
analysis and its per-recognizer regex-internal witnesses are obsolete and live in
git history.

The per-lens token-native baselines (and the no-detection-regression checks vs
the former recognizer) live in:
  * tests/test_fi_token_actor_modal.py
  * tests/test_fi_token_delegation.py
  * tests/test_fi_token_procedure.py
  * tests/test_fi_token_sanction.py

This module keeps only the generic substrate-availability sanity: both the
TokenTape and the MorphOverlay are populated on real-statute units, so the
token-native lenses always have their required view in scope.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.core.legal_surface_tokens import MorphOverlay, TokenTape
from lawvm.finland.legal_surface.bundle import build_surface_bundle


def _canonical_corpus_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "finlex.farchive").exists()


_REAL_SIDS: tuple[str, ...] = (
    "2002/723",
    "1999/731",
    "2003/434",
    "2009/916",
    "1889/39",
    "2011/379",
)


def _real_xml(sid: str) -> bytes | None:
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore

    root = os.environ["LAWVM_CANONICAL_DATA_ROOT"]
    store = TransparentCorpusStore(
        Farchive(str(Path(root) / "data" / "finlex.farchive"), readonly=True),
        cache_only=True,
    )
    try:
        return store.read_source(sid) or store.read_amendment(sid)
    finally:
        store.close()


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_substrate_views_populated_on_real_units() -> None:
    """Real-statute units carry BOTH the token tape and the morph overlay — the
    views the token-native frame lenses require."""
    checked = 0
    for sid in _REAL_SIDS:
        xb = _real_xml(sid)
        if not xb:
            continue
        unit = build_surface_bundle(xb, sid).units[0]
        assert isinstance(unit.token_tape, TokenTape)
        assert isinstance(unit.morph_overlay, MorphOverlay)
        checked += 1
        if checked >= 5:
            break
    assert checked >= 5, f"needed ≥5 real statutes, got {checked}"
