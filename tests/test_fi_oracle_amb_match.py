"""Tests for the ``amb`` (nondeterministic) oracle-version match.

The Finlex oracle's version SELECTION is unreliable: a slot can carry several
``<section>`` versions and the consolidation may label the wrong one in force.
When replay's text matches a genuine-but-not-chosen oracle version of the SAME
slot, the bench forgives the penalty (source-attested, not fabricated) and emits
a warning — it never masks fabricated or cross-provision text (exact same-slot
equality only). These tests pin that contract at the pure-function level; the
end-to-end bench wiring is exercised by the corpus bench + the reconciliation
invariant below.
"""
from __future__ import annotations

import re

import pytest
from lxml import etree

from lawvm.core.bench_contract import (
    BenchStatus,
    BenchUnitResult,
    check_residue_reconciliation,
)
from lawvm.tools.section_keys import (
    OracleAmbAlternate,
    OracleAmbCandidates,
    _MAX_AMB_ALTERNATES_PER_SECTION,
    extract_oracle_section_alternates,
    oracle_amb_alternate_match,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _clean(text: str) -> str:
    """Stand-in for the bench cleaner: strip to alphanumerics, lowercased."""
    return re.sub(r"[^a-z0-9äöå]", "", text.lower())


def _two_version_oracle(*, current: str, shadow: str) -> etree._Element:
    return etree.fromstring(
        f"""<akomaNtoso xmlns="{_AKN}"><act><body>
          <section eId="sec_6v20260143"><num>6 §</num>
            <content><p>{current}</p></content></section>
          <section eId="sec_6v20250029"><num>6 §</num>
            <content><p>{shadow}</p></content></section>
        </body></act></akomaNtoso>""".encode()
    )


def test_section_level_alternates_captured() -> None:
    root = _two_version_oracle(current="Uusi teksti.", shadow="Vanha teksti.")
    alts = extract_oracle_section_alternates(root)
    assert "section:6" in alts
    cands = alts["section:6"]
    assert cands.chosen_version == 20260143  # highest wins as "current"
    assert len(cands.alternates) == 1
    assert cands.alternates[0].version == 20250029
    assert "Vanha teksti" in cands.alternates[0].text


def test_single_version_has_no_alternates() -> None:
    root = etree.fromstring(
        f"""<akomaNtoso xmlns="{_AKN}"><act><body>
          <section eId="sec_3v20250029"><num>3 §</num>
            <content><p>Ainoa teksti.</p></content></section>
        </body></act></akomaNtoso>""".encode()
    )
    assert extract_oracle_section_alternates(root) == {}


def test_match_returns_witness_for_shadow_version() -> None:
    root = _two_version_oracle(current="Uusi teksti.", shadow="Vanha teksti.")
    cands = extract_oracle_section_alternates(root)["section:6"]
    # replay reproduced the SHADOW wording (the oracle mislabeled in-force)
    replay_clean = _clean("6 § Vanha teksti.")
    witness = oracle_amb_alternate_match("section:6", replay_clean, cands, _clean)
    assert witness is not None
    assert "matched=@20250029" in witness
    assert "chosen=@20260143" in witness
    assert "key=section:6" in witness


def test_match_none_for_unattested_text() -> None:
    root = _two_version_oracle(current="Uusi teksti.", shadow="Vanha teksti.")
    cands = extract_oracle_section_alternates(root)["section:6"]
    # replay text matches NEITHER attested version -> stays penalized
    replay_clean = _clean("6 § Täysin eri teksti.")
    assert oracle_amb_alternate_match("section:6", replay_clean, cands, _clean) is None


def test_match_none_when_no_candidates() -> None:
    assert oracle_amb_alternate_match("section:6", _clean("x"), None, _clean) is None


def test_match_none_for_empty_replay() -> None:
    cands = OracleAmbCandidates(
        chosen_version=20260143,
        alternates=(OracleAmbAlternate(version=20250029, text="6 § Vanha teksti."),),
    )
    assert oracle_amb_alternate_match("section:6", "", cands, _clean) is None


def test_alternates_capped() -> None:
    versions = "".join(
        f'<section eId="sec_6v2025{i:04d}"><num>6 §</num>'
        f"<content><p>Versio {i}.</p></content></section>"
        for i in range(20)
    )
    root = etree.fromstring(
        f'<akomaNtoso xmlns="{_AKN}"><act><body>{versions}</body></act></akomaNtoso>'.encode()
    )
    cands = extract_oracle_section_alternates(root)["section:6"]
    assert len(cands.alternates) <= _MAX_AMB_ALTERNATES_PER_SECTION


def test_reconciliation_holds_when_amb_neutralizes_sole_diff() -> None:
    # amb forgiving the only divergence -> structural_err 0, residue empty, and a
    # witness recording the forgiven version disagreement. The witness must ride
    # `witnesses`, NOT `residue_buckets`, so reconciliation (structural_err>0 ⟺
    # residue nonempty) still holds.
    result = BenchUnitResult(
        unit_id="2015/1286",
        status=BenchStatus.SCORED,
        structural_err=0.0,
        text_err=0.0,
        residue_buckets={},
        witnesses=(
            "oracle_version_selection_alternate_match key=section:6 "
            "matched=@20250029 chosen=@20260143",
        ),
    )
    check_residue_reconciliation(result)  # must not raise


@pytest.mark.xfail(reason="child-level (subsection/paragraph) version shadows not yet surfaced as amb alternates", strict=True)
def test_child_level_version_shadow_alternates_TODO() -> None:
    # A single <section> whose CHILD slot carries two versions: the dropped child
    # shadow is not yet exposed as a section-level alternate, so a replay matching
    # it is not (yet) forgiven. Documents the deferred extension.
    root = etree.fromstring(
        f"""<akomaNtoso xmlns="{_AKN}"><act><body>
          <section eId="sec_7v20250029"><num>7 §</num>
            <subsection eId="sec_7__subsec_1v20260143"
              xmlns:finlex="http://data.finlex.fi/schema/finlex"
              finlex:originalVersion="@20260143" finlex:originalVersionLabel="19.2.2026/143">
              <content><p>Uusi momentti.</p></content></subsection>
            <subsection eId="sec_7__subsec_1v20250029"
              xmlns:finlex="http://data.finlex.fi/schema/finlex"
              finlex:originalVersion="@20250029" finlex:originalVersionLabel="23.1.2025/29">
              <content><p>Vanha momentti.</p></content></subsection>
          </section>
        </body></act></akomaNtoso>""".encode()
    )
    cands = extract_oracle_section_alternates(root).get("section:7")
    assert cands is not None  # xfail: child-level not captured -> None today
