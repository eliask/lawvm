"""E2E corpus invariant: reference byte spans are correctly anchored.

This is the corpus-wide backstop for the reference-span family of bugs. A single
synthetic fixture (see ``test_fi_internal_ref_span_boundary.py``) proves ONE
shape; this sweep proves the invariant holds across real statutes, where the
failure mode is statistical (~thousands of mentions corpus-wide).

Two invariants per emitted mention that carries a ``source_span``:

  1. IN-BOUNDS — the span lies within ``xml_bytes``.
  2. LEFT-NUMBER-BOUNDARY — a surface that begins with a digit (a
     section/momentti/article NUMBER, e.g. ``"56 §:ssä"``) must NOT be anchored
     immediately after another digit. A naive ``bytes.find`` re-anchors
     ``"56 §:ssä"`` onto the tail of an earlier ``"156 §:ssä"``; the slice still
     READS "56 §:ssä" (so a text-equality check misses it) but points at the
     WRONG occurrence, so the viewer renders the link on the wrong span. This is
     the digit-prefix-embedding class that left ``Edellä 56 §:ssä`` unlinked.

The sweep is deterministic (seeded sample) and opt-in: it skips cleanly when the
Finlex corpus is not available in the environment.
"""
from __future__ import annotations

import random

import pytest

from lawvm.finland.references.ref_mention_extractor import (
    extract_all_reference_mentions,
)

#: Bounded, deterministic sample size — large enough to surface a ~1% class,
#: small enough for CI.
_SWEEP_N = 400


def _corpus_or_skip():
    try:
        from lawvm.finland.corpus import get_corpus_store

        store = get_corpus_store()
        ids = store.list_statute_ids()
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Finlex corpus unavailable: {exc}")
    if not ids:
        pytest.skip("Finlex corpus returned no statute ids")
    return store, ids


def _statute_xml(store, sid: str) -> bytes | None:
    xml = store.read_source(sid)
    if not xml:
        xml = store.read_amendment(sid)
    return xml


def test_reference_spans_are_not_mis_anchored_into_longer_numbers() -> None:
    """No reference span is lodged inside a longer number across the corpus."""
    store, ids = _corpus_or_skip()
    rng = random.Random(404)
    sample = rng.sample(ids, min(_SWEEP_N, len(ids)))

    embedded: list[str] = []
    out_of_bounds: list[str] = []
    swept = 0
    for sid in sample:
        xml = _statute_xml(store, sid)
        if not xml:
            continue
        try:
            mentions = extract_all_reference_mentions(xml, sid).mentions
        except Exception:
            continue
        swept += 1
        for m in mentions:
            sp = getattr(m, "source_span", None)
            if sp is None:
                continue
            bo, bl = sp.byte_offset, sp.byte_len
            if bo < 0 or bo + bl > len(xml):
                out_of_bounds.append(f"{sid}: offset={bo} len={bl} size={len(xml)}")
                continue
            sliced = xml[bo : bo + bl]
            # Left-number-boundary: a digit-leading surface must not sit right
            # after another digit (= embedded in a longer number run).
            if sliced[:1].isdigit() and bo > 0 and xml[bo - 1 : bo].isdigit():
                ctx = xml[max(0, bo - 14) : bo + bl + 4].decode("utf-8", "replace")
                embedded.append(f"{sid}: '{sliced.decode('utf-8','replace')}' in ...{ctx}...")

    assert swept > 0, "swept zero statutes (corpus empty or unreadable)"
    assert not out_of_bounds, (
        f"{len(out_of_bounds)} reference span(s) out of bounds:\n  "
        + "\n  ".join(out_of_bounds[:10])
    )
    assert not embedded, (
        f"{len(embedded)} reference span(s) mis-anchored inside a longer number "
        f"over a {swept}-statute sweep (expected 0 — the 56-in-156 class):\n  "
        + "\n  ".join(embedded[:15])
    )
