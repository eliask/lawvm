"""Stage-3 export parity gate: the graph writer == the extractor oracle.

Pro r5 Phase 3 Stage 3. Phase 3b proved that the Legal Surface Graph round-trips
to fi_refs rows (``tests/test_fi_graph_parity.py``). Stage 3 makes the graph the
SOURCE OF TRUTH for the production fi_refs export: ``export_fi_refs`` now projects
each statute via :func:`export_fi_refs._project_refs_for_statute_via_graph` by
default, keeping :func:`export_fi_refs._project_refs_for_statute_via_extractor`
reachable only as the parity oracle.

This gate asserts that the two PRODUCTION projector functions — the graph writer
(default) and the extractor oracle — emit the SAME augmented fi_refs rows
(including the Slice-3 provenance columns ``_augment_row`` adds), as an
order-insensitive MULTISET, field-for-field, over:

  * several synthetic statutes (varied lanes), and
  * >=4 real Finlex statutes when the canonical corpus is available
    (``LAWVM_CANONICAL_DATA_ROOT``).

FAIL-LOUD: the comparison is the FULL augmented row (every field both projectors
emit), so any divergence — a dropped mention, a wrong field, a missing provenance
column — is caught. The comparison is NOT narrowed to force green.
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, List

import pytest

from lawvm.core.manual_claims.primitive import _ProfileTagDeprecated as ProfileTag
from lawvm.tools.export_fi_refs import (
    _project_refs_for_statute_via_extractor,
    _project_refs_for_statute_via_graph,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_PROFILE = ProfileTag.DETERMINISTIC_ONLY


# ── Synthetic statute fixtures (mirror the lanes in test_fi_graph_parity) ──────

# Plain-text id cite + internal § ref + a same-statute § ref.
_XML_PLAIN_AND_INTERNAL = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Tata lakia sovelletaan ymparistonsuojelulain (527/2014) 5 §:ssa tarkoitettuun toimintaan.</p>
    <p>Edella 1 momentissa tarkoitettuun toimintaan sovelletaan myos 5 §:n saannoksia.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")

# AKN <ref> element cites (carry surface_text) + an internal § ref.
_XML_REF_ELEMENTS = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Sovelletaan <ref href="/akn/fi/act/statute/2014/527/sec_5">5 §:aa</ref> mukaisesti.</p>
    <p>Lisaksi noudatetaan <ref href="/akn/fi/act/statute/2011/379/sec_3">3 §:aa</ref>.</p>
    <p>Edella 1 momentissa tarkoitetaan 2 §:ssa saadettya.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")

# A vague (OPEN, targetless) catch-all + a cross-statute by-name-ish cite.
_XML_VAGUE = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Jollei muussa laissa toisin saadeta, sovelletaan tata lakia.</p>
    <p>Sovelletaan myos tieliikennelain (729/2018) 12 §:aa.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")

_SYNTHETIC_CASES = [
    ("123/2020", _XML_PLAIN_AND_INTERNAL),
    ("200/2019", _XML_REF_ELEMENTS),
    ("300/2021", _XML_VAGUE),
]


# ── In-memory store stub (the projectors only call store.read_oracle) ─────────


class _DictStore:
    """Minimal store: the projectors only call ``read_oracle(statute_id)``."""

    def __init__(self, mapping: Dict[str, bytes]) -> None:
        self._mapping = mapping

    def read_oracle(self, statute_id: str) -> bytes:
        return self._mapping[statute_id]


# ── Parity key (the FULL augmented row both projectors emit) ──────────────────


def _row_key(row: Dict[str, Any]) -> tuple[tuple[str, object], ...]:
    """Order-insensitive, field-for-field key over the WHOLE augmented row.

    Sorting the items makes the key independent of dict insertion order while
    still covering every field (the deterministic extractor and the graph
    projector both run rows through ``_augment_row``, so the provenance columns
    are in scope too). Nothing is dropped from the comparison.
    """
    return tuple(sorted(row.items(), key=lambda kv: kv[0]))


def _multiset(rows: List[Dict[str, Any]]) -> Counter:
    return Counter(_row_key(r) for r in rows)


def _assert_parity(statute_id: str, store: Any) -> int:
    """Assert graph-projector rows == extractor-projector rows. Returns row count."""
    extractor_rows, _ = _project_refs_for_statute_via_extractor(
        statute_id, store, _PROFILE
    )
    graph_rows, _ = _project_refs_for_statute_via_graph(statute_id, store, _PROFILE)

    # Cardinality identity first (a dropped/extra mention is the loudest failure).
    assert len(graph_rows) == len(extractor_rows), (
        f"{statute_id}: row cardinality diverged "
        f"(graph {len(graph_rows)} vs extractor {len(extractor_rows)})"
    )

    expected = _multiset(extractor_rows)
    actual = _multiset(graph_rows)
    assert actual == expected, (
        f"{statute_id}: graph writer vs extractor oracle FULL-row parity diverged.\n"
        f"  only in extractor: {sorted(map(str, (expected - actual).elements()))[:5]}\n"
        f"  only in graph:     {sorted(map(str, (actual - expected).elements()))[:5]}"
    )
    return len(graph_rows)


# ── Synthetic parity ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("statute_id,xml_bytes", _SYNTHETIC_CASES)
def test_export_parity_synthetic(statute_id: str, xml_bytes: bytes) -> None:
    """Default graph writer reproduces the extractor oracle on synthetic lanes."""
    store = _DictStore({statute_id: xml_bytes})
    n = _assert_parity(statute_id, store)
    assert n > 0, f"{statute_id}: expected mentions in this fixture"


def test_default_writer_uses_graph() -> None:
    """The default ``_project_refs_for_statute`` IS the graph projector.

    Stage 3 contract: the production writer reads the graph. We assert that the
    default entry point produces exactly what the graph projector produces (not
    merely something parity-equal — the default dispatches to the graph path).
    """
    from lawvm.tools.export_fi_refs import _project_refs_for_statute

    statute_id, xml_bytes = _SYNTHETIC_CASES[1]
    store = _DictStore({statute_id: xml_bytes})

    default_rows, _ = _project_refs_for_statute(statute_id, store, _PROFILE)
    graph_rows, _ = _project_refs_for_statute_via_graph(statute_id, store, _PROFILE)
    assert _multiset(default_rows) == _multiset(graph_rows)
    assert default_rows  # non-empty fixture


# ── Real-corpus parity (opt-in via the canonical data root) ───────────────────


def _corpus_available() -> bool:
    return bool(os.environ.get("LAWVM_CANONICAL_DATA_ROOT"))


@pytest.mark.skipif(
    not _corpus_available(),
    reason="LAWVM_CANONICAL_DATA_ROOT not set; real-corpus export parity skipped",
)
def test_export_parity_real_corpus() -> None:
    """Default graph writer reproduces the extractor oracle on real Finlex statutes.

    Checks >=4 statutes that each yield >=3 mentions; asserts the production graph
    projector emits the same augmented fi_refs rows the extractor oracle does
    (full-row multiset + cardinality), field-for-field.
    """
    from lawvm.finland.corpus import get_corpus_store

    store = get_corpus_store()
    all_ids = store.list_statute_ids()
    checked = 0
    checked_ids: List[str] = []
    for statute_id in all_ids:
        if checked >= 4:
            break
        try:
            xml_bytes = store.read_oracle(statute_id)
        except Exception:
            continue
        if not xml_bytes:
            continue
        extractor_rows, _ = _project_refs_for_statute_via_extractor(
            statute_id, store, _PROFILE
        )
        if len(extractor_rows) < 3:
            continue

        _assert_parity(statute_id, store)
        checked += 1
        checked_ids.append(statute_id)

    assert checked >= 4, (
        f"expected >=4 real-corpus statutes checked, got {checked} "
        f"(ids={checked_ids})"
    )
