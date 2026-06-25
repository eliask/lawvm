"""Committed load-bearing guard for the two FI heading-prefix strippers.

The two comparison-only heading-prefix strippers in
:mod:`lawvm.finland.oracle_comparison` —
:func:`strip_legacy_roman_division_heading_prefix` and
:func:`strip_legacy_numbered_section_heading_prefix` — used to be decorated
``@deprecated`` (PEP 702), as if they were strangled legacy lanes. They are NOT:
a full-corpus pass proves each performs real text mutations, in BOTH oracle modes
(``official_consolidation`` and ``legal_pit``), on real corpus statutes — they are
REQUIRED, load-bearing source-projection residue. This module is the committed
evidence that justifies retaining them (mirroring the regenerable-guard pattern of
:mod:`tests.test_fi_fallback_coverage_census`):

* both strippers are *de-deprecated* (carry no ``__deprecated__`` marker) — the
  annotation was MISLABELED;
* each stripper, reached through its OWNING production composite
  :func:`strip_non_substantive_source_projection_residue`, performs >= 1 real
  mutation on a pinned corpus witness, in BOTH oracle modes;
* the mutation is *attributable* to the named stripper (the other stripper is a
  no-op on that witness), so a single stripper going inert is caught.

Pinned witnesses (found by running the strippers over the whole canonical corpus
and capturing the heading-prefix mutations):

* numbered: statute ``1932/242`` (vekselilaki) §64 (``1. Eri kappaleet
  vekseliä.`` dropped) and §67 (``2. Vekselinjäljennökset.`` dropped);
* roman: statute ``1993/1055`` (kirkkolaki) §13 (``C. Kaste`` dropped) and §18
  (``D. Avioliittoon vihkiminen`` dropped).

The corpus census is regenerable: it drives the real production replay
entrypoint (``replay_xml`` -> materialized IR -> section text) and the real
production composite, so re-running it after a corpus or replay change
re-establishes the witnesses. The guard FAILS if either stripper becomes inert
(a future no-op regression) — that is the whole point: it is the load-bearing
evidence, not a smoke test.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import pytest

from lawvm.finland.oracle_comparison import (
    strip_legacy_numbered_section_heading_prefix,
    strip_legacy_roman_division_heading_prefix,
    strip_non_substantive_source_projection_residue,
)

# ---------------------------------------------------------------------------
# Pinned witnesses. A human re-derives these by re-running the corpus census
# (drive the production composite over every replayed section and capture the
# heading-prefix mutations) if the corpus/replay legitimately changes.
# ---------------------------------------------------------------------------
ORACLE_MODES: tuple[Literal["official_consolidation", "legal_pit"], ...] = (
    "official_consolidation",
    "legal_pit",
)

#: Numbered-stripper witness: vekselilaki, the numbered subdivision labels
#: ``1. Eri kappaleet vekseliä.`` / ``2. Vekselinjäljennökset.``
NUMBERED_WITNESS_SID = "1932/242"
NUMBERED_WITNESS_SECTION_KEYS = (
    "part:1/chapter:9/section:64",
    "part:1/chapter:9/section:67",
)

#: Roman-stripper witness: kirkkolaki, the Roman division headings ``C. Kaste``
#: / ``D. Avioliittoon vihkiminen``.
ROMAN_WITNESS_SID = "1993/1055"
ROMAN_WITNESS_SECTION_KEYS = (
    "part:2/chapter:2/section:13",
    "part:2/chapter:2/section:18",
)


# ---------------------------------------------------------------------------
# De-deprecation (corpus-free, always runs): the strippers must NOT carry the
# PEP 702 @deprecated marker any more — they are required residue.
# ---------------------------------------------------------------------------
def test_strippers_are_not_deprecated() -> None:
    """The two strippers are required residue, so they must be de-deprecated."""
    for fn in (
        strip_legacy_roman_division_heading_prefix,
        strip_legacy_numbered_section_heading_prefix,
    ):
        assert getattr(fn, "__deprecated__", None) is None, (
            f"{fn.__name__} still carries the @deprecated marker; it is required, "
            "load-bearing source-projection residue (see this module), not a "
            "strangled legacy lane."
        )


def test_composite_owns_both_strippers() -> None:
    """A targeted, corpus-free attribution check on synthetic witness text.

    This is the corpus-free floor: it proves the OWNING composite delegates to
    each stripper and that each stripper fires independently on its own shape.
    The archive-gated census below re-establishes the same facts on real corpus
    text. Mutations here are exact text drops, so the assertions are precise.
    """
    # Numbered heading-prefix shape: composite drops it; only the numbered
    # stripper is responsible (roman is a no-op).
    numbered = "67 § 2. Vekselinjäljennökset. Jokaisella vekselin haltijalla on oikeus."
    assert strip_non_substantive_source_projection_residue(numbered) != numbered
    assert strip_legacy_numbered_section_heading_prefix(numbered) != numbered
    assert strip_legacy_roman_division_heading_prefix(numbered) == numbered

    # Roman division-heading shape: composite drops it; only the roman stripper
    # is responsible (numbered is a no-op).
    roman = "13 § C. Kaste. Oikein kastettua ei saa kastaa uudelleen."
    assert strip_non_substantive_source_projection_residue(roman) != roman
    assert strip_legacy_roman_division_heading_prefix(roman) != roman
    assert strip_legacy_numbered_section_heading_prefix(roman) == roman


# ---------------------------------------------------------------------------
# Corpus census guard (archive-gated). Drives the production replay entrypoint
# and the production composite over the pinned witness statutes, in BOTH oracle
# modes, and asserts each stripper is load-bearing (>= 1 real mutation) with
# clean attribution. FAILS if a stripper goes inert.
# ---------------------------------------------------------------------------
def _canonical_corpus_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "finlex.farchive").exists()


def _replay_section_texts(
    sid: str, mode: Literal["official_consolidation", "legal_pit"]
) -> dict[str, str]:
    """Render replay section texts for ``sid`` via the production replay path.

    Mirrors the production section-comparison path in
    :func:`lawvm.tools.oracle_check._classify_statute`: replay the statute, take
    the materialized IR sections, and render each to text.
    """
    from lawvm.core.ir_helpers import irnode_to_text
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import (
        ReplayXmlRequest,
        ReplayXmlSinks,
        call_replay_xml,
    )
    from lawvm.tools.section_keys import extract_ir_sections

    compiled_ops: list = []
    failed_ops: list = []
    lo_ops: list = []
    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(parent_id=sid, mode=mode, quiet=True),
        sinks=ReplayXmlSinks(
            compiled_ops_out=compiled_ops,
            failed_ops_out=failed_ops,
            lo_ops_out=lo_ops,
        ),
    )
    sections = extract_ir_sections(master.materialized_state.ir)
    return {key: irnode_to_text(node) for key, node in sections.items()}


def _assert_witness_load_bearing(
    *,
    sid: str,
    section_keys: tuple[str, ...],
    responsible,
    other,
) -> None:
    """Assert ``responsible`` is load-bearing on the witness, in BOTH modes.

    For every pinned witness section, in every oracle mode:
      * the OWNING composite mutates the replay section text (the production
        path actually strips it);
      * the ``responsible`` stripper is the one that mutates it;
      * the ``other`` stripper is a no-op on it (clean attribution, so a single
        stripper going inert is caught here, not masked by the sibling).
    At least one witness section must be exercised per mode (the census is not
    vacuously green if the witness silently disappears from the corpus).
    """
    for mode in ORACLE_MODES:
        texts = _replay_section_texts(sid, mode)
        exercised = 0
        for key in section_keys:
            text = texts.get(key)
            if text is None:
                continue
            exercised += 1
            assert strip_non_substantive_source_projection_residue(text) != text, (
                f"{sid} {mode} {key}: owning composite is INERT on a pinned "
                f"witness — {responsible.__name__} is no longer load-bearing."
            )
            assert responsible(text) != text, (
                f"{sid} {mode} {key}: {responsible.__name__} is INERT on its "
                "pinned witness; it is required residue and must mutate it."
            )
            assert other(text) == text, (
                f"{sid} {mode} {key}: {other.__name__} unexpectedly mutated the "
                f"{responsible.__name__} witness — attribution is no longer clean; "
                "re-derive the pinned witnesses."
            )
        assert exercised >= 1, (
            f"{sid} {mode}: none of the pinned witness sections "
            f"{section_keys} were present in replay — re-derive the witnesses "
            "(the census must not pass vacuously)."
        )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
@pytest.mark.slow
def test_numbered_stripper_is_load_bearing_on_corpus() -> None:
    _assert_witness_load_bearing(
        sid=NUMBERED_WITNESS_SID,
        section_keys=NUMBERED_WITNESS_SECTION_KEYS,
        responsible=strip_legacy_numbered_section_heading_prefix,
        other=strip_legacy_roman_division_heading_prefix,
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
@pytest.mark.slow
def test_roman_stripper_is_load_bearing_on_corpus() -> None:
    _assert_witness_load_bearing(
        sid=ROMAN_WITNESS_SID,
        section_keys=ROMAN_WITNESS_SECTION_KEYS,
        responsible=strip_legacy_roman_division_heading_prefix,
        other=strip_legacy_numbered_section_heading_prefix,
    )
