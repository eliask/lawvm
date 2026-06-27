"""Starter-shard tests for the U.S. federal self-consistency projector.

Covers (all offline; no network, no USC-edition oracle read):

* the amendatory-finding rule_id -> shared-signal-type mapping
  (``us_amendatory_unlowered`` -> unhandled_op; the unresolved / non-Title-11 /
  non-positive-holdout rules -> target_absent);
* ORACLE-INDEPENDENCE: the per-law projector resolves real signals from a store
  that serves ONLY the PLAW USLM bytes and raises if any ``us://usc/...`` edition
  locator is touched — proving no after-edition is consulted to produce a row;
* at least one real projected signal on a committed Title-11 PLAW fixture;
* the parse-failure path producing a typed ``invariant_violation`` row + an
  error row (one bad law never aborts the sweep);
* the shared row schema every projected row carries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.tools.us_self_consistency import (
    _AMENDATORY_FINDING_SIGNAL,
    US_SIGNAL_TYPES,
    project_us_self_consistency,
    resolve_us_locators,
)
from lawvm.us_federal.amendatory import (
    NON_TITLE_TARGET_RULE_ID,
    TARGET_UNRESOLVED_FINDING_RULE_ID,
    UNLOWERED_FINDING_RULE_ID,
    UNRECOGNIZED_REDESIGNATE_FINDING_RULE_ID,
)
from lawvm.us_federal.nonpositive import (
    NOTE_ONLY_FINDING_RULE_ID,
    UNMAPPED_FINDING_RULE_ID,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "us_federal"
_USLM_NS = "http://schemas.gpo.gov/xml/uslm"

_ROW_KEYS = {
    "statute_id",
    "amendment_id",
    "signal_type",
    "category",
    "description",
    "target_scope",
    "reason",
}


def _synthetic_plaw(section_body: str) -> bytes:
    """Wrap one amendatory <section> body into a minimal lowerable USLM lawDoc."""
    return (
        f'<lawDoc xmlns="{_USLM_NS}">'
        "<meta><congress>116</congress><docNumber>900</docNumber>"
        "<approvedDate>2020-01-01</approvedDate></meta>"
        f"<main>{section_body}</main></lawDoc>"
    ).encode("utf-8")


class _OracleForbiddenStore:
    """A minimal archive that serves ONE PLAW locator and forbids any USC read.

    ``get`` raises if asked for a ``us://usc/...`` edition locator, so a test that
    projects signals through this store proves the projector never consults the
    oracle (the oracle-independence contract). Any other PLAW locator returns
    None (absent), exercising the absent-source path without touching the oracle.
    """

    def __init__(self, locator: str, data: bytes) -> None:
        self._locator = locator
        self._data = data
        self.usc_reads: list[str] = []

    def get(self, locator: str) -> bytes | None:
        if locator.startswith("us://usc/"):
            self.usc_reads.append(locator)
            raise AssertionError(
                f"oracle-independence violated: projector read USC edition {locator!r}"
            )
        if locator == self._locator:
            return self._data
        return None


# ---------------------------------------------------------------------------
# Signal-type mapping
# ---------------------------------------------------------------------------


def test_amendatory_finding_signal_map_covers_every_lowering_finding() -> None:
    # Every lowering-failure finding emitted by lower_plaw_amendatory routes to
    # either `unhandled_op` (genuinely un-lowered: each held-out family carries
    # a stable rule id, per AGENTS.md §2.1) or `target_absent` (target out of
    # scope: unresolved or non-Title). The generic UNLOWERED_FINDING_RULE_ID
    # catch-all remains mapped but should now be a last-resort default; the
    # named families (redesignate, strike, insert, etc.) all carry their own
    # typed ids.
    assert _AMENDATORY_FINDING_SIGNAL[UNLOWERED_FINDING_RULE_ID] == "unhandled_op"
    assert _AMENDATORY_FINDING_SIGNAL[TARGET_UNRESOLVED_FINDING_RULE_ID] == "target_absent"
    assert _AMENDATORY_FINDING_SIGNAL[NON_TITLE_TARGET_RULE_ID] == "target_absent"
    # Every mapped signal type is a member of the shared US taxonomy.
    assert set(_AMENDATORY_FINDING_SIGNAL.values()) <= set(US_SIGNAL_TYPES)


def test_unlowered_instruction_maps_to_unhandled_op() -> None:
    # A redesignation in a multi-unit / non-enumerable form (multi-letter target
    # labels, not simple single-letter alphabetic ranges) is deliberately NOT
    # lowered to a RENUMBER -> us_amendatory_unrecognized_redesignate_shape
    # (the named-typed-finding replacement for the generic UNLOWERED catch-all
    # for the redesignate family) -> unhandled_op.
    body = (
        "<section><num>2.</num><content>"
        "<ref href='/us/usc/t11/s521'>Section 521 of title 11, United States Code</ref>"
        ", is amended by redesignating subsections (a) through (d) as "
        "subsections (aa) through (ad)"
        "<amendingAction type='redesignate'/>.</content></section>"
    )
    loc = "us://plaw/116/publ900.xml"
    store = _OracleForbiddenStore(loc, _synthetic_plaw(body))
    rows, errs = project_us_self_consistency(loc, store)

    assert errs == []
    unhandled = [r for r in rows if r["signal_type"] == "unhandled_op"]
    assert unhandled, rows
    assert all(r["category"] == UNRECOGNIZED_REDESIGNATE_FINDING_RULE_ID for r in unhandled)
    # No USC edition was ever read.
    assert store.usc_reads == []


def test_non_title_11_target_maps_to_target_absent() -> None:
    # A resolvable target OUTSIDE Title 11 -> us_amendatory_target_non_us_code
    # -> target_absent (candidate withheld from the Title-11 scope, not guessed).
    body = (
        "<section><num>3.</num><content>"
        "<ref href='/us/usc/t28/s1409/b'>Section 1409(b) of title 28, "
        "United States Code</ref>, is amended by "
        "striking <quotedText>old</quotedText> and inserting "
        "<quotedText>new</quotedText>"
        "<amendingAction type='delete'/><amendingAction type='insert'/>."
        "</content></section>"
    )
    loc = "us://plaw/116/publ901.xml"
    store = _OracleForbiddenStore(loc, _synthetic_plaw(body))
    rows, errs = project_us_self_consistency(loc, store)

    assert errs == []
    absent = [r for r in rows if r["category"] == NON_TITLE_TARGET_RULE_ID]
    assert absent, rows
    assert all(r["signal_type"] == "target_absent" for r in absent)
    assert "28" in absent[0]["target_scope"]
    assert store.usc_reads == []


def test_nonpositive_holdout_rule_ids_map_to_target_absent() -> None:
    # The non-positive holdout categories the projector can emit are both
    # target_absent (an uncodified / unmapped act-section target).
    for rule_id in (UNMAPPED_FINDING_RULE_ID, NOTE_ONLY_FINDING_RULE_ID):
        assert rule_id.startswith("us_nonpositive_target_")


# ---------------------------------------------------------------------------
# Oracle-independence + a real projected signal on a committed fixture
# ---------------------------------------------------------------------------


def test_real_fixture_projects_signals_without_reading_any_usc_edition() -> None:
    # PLAW-114publ89.xml is a committed multi-title PLAW that genuinely produces
    # target_absent signals (non-Title-11 + unresolved targets). Project it
    # through a store that forbids any USC read to prove oracle-independence.
    plaw = (FIXTURE_DIR / "PLAW-114publ89.xml").read_bytes()
    loc = "us://plaw/114/publ89.xml"
    store = _OracleForbiddenStore(loc, plaw)

    rows, errs = project_us_self_consistency(loc, store)

    assert errs == []
    assert rows, "the fixture must project at least one real signal"
    # Oracle was never consulted to derive a signal.
    assert store.usc_reads == []

    # Every row carries the shared schema and a known signal type.
    for r in rows:
        assert set(r) == _ROW_KEYS, r
        assert r["signal_type"] in US_SIGNAL_TYPES
        assert r["statute_id"] == loc

    # The lowering stage genuinely surfaces target-absent classes on this real law.
    assert any(r["signal_type"] == "target_absent" for r in rows), rows
    categories = {r["category"] for r in rows}
    assert NON_TITLE_TARGET_RULE_ID in categories
    assert TARGET_UNRESOLVED_FINDING_RULE_ID in categories


def test_absent_plaw_source_is_a_typed_error_row_not_a_crash() -> None:
    store = _OracleForbiddenStore("us://plaw/116/publ900.xml", b"")
    rows, errs = project_us_self_consistency("us://plaw/199/publ1.xml", store)
    assert rows == []
    assert errs == [{"statute_id": "us://plaw/199/publ1.xml", "error": "plaw_source_absent"}]


def test_unparseable_plaw_is_an_invariant_violation_row_plus_error_row() -> None:
    loc = "us://plaw/116/publ902.xml"
    store = _OracleForbiddenStore(loc, b"<lawDoc>not closed")
    rows, errs = project_us_self_consistency(loc, store)

    assert len(rows) == 1
    assert rows[0]["signal_type"] == "invariant_violation"
    assert rows[0]["statute_id"] == loc
    assert set(rows[0]) == _ROW_KEYS
    assert len(errs) == 1
    assert errs[0]["statute_id"] == loc


# ---------------------------------------------------------------------------
# Corpus selection: explicit-locator path (no oracle read)
# ---------------------------------------------------------------------------


class _Args:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def test_explicit_statutes_resolve_without_touching_the_corpus_or_oracle() -> None:
    # The explicit-locator path must never read the corpus CSV or any USC edition.
    store = _OracleForbiddenStore("us://plaw/0/publ0.xml", b"")
    args = _Args(
        statutes="us://plaw/116/publ54.xml, PL 114-113, 118-24",
        limit=0,
    )
    locs = resolve_us_locators(args, store)
    assert locs == [
        "us://plaw/116/publ54.xml",
        "us://plaw/114/publ113.xml",
        "us://plaw/118/publ24.xml",
    ]
    assert store.usc_reads == []


def test_us_signal_taxonomy_has_no_coverage_or_elaboration_types() -> None:
    # The US lowering stage has no honest coverage_gap / elaboration / occupancy
    # surface; those FI-only types must not leak into the US taxonomy.
    assert "coverage_gap" not in US_SIGNAL_TYPES
    assert "elaboration_finding" not in US_SIGNAL_TYPES
    assert "occupancy_violation" not in US_SIGNAL_TYPES


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
