"""Tests for the parse-bench grammar-coverage tool (corpus-independent core).

Covers the FI amendment/enactment split, the EE label-coverage audit
(``estonia.coverage_audit``) on synthetic inputs, and the parse-bench
jurisdiction dispatch (structured-jurisdiction guard).  None of these touch the
corpus or any parser hot path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import pytest

from lawvm.estonia.coverage_audit import (
    TIER_UNMATCHED_SECTION,
    TIER_VERB_NO_OP,
    audit_amendment_labels,
    mentioned_labels,
    produced_labels,
)
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


# ---------------------------------------------------------------------------
# EE label-coverage audit (synthetic op-items + synthetic produced ops)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _StubAddr:
    path: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class _StubOp:
    """Minimal stand-in for a produced LegalOperation (target/destination only)."""

    target: Optional[_StubAddr] = None
    destination: Optional[_StubAddr] = None


def _section(*levels: Tuple[str, str]) -> _StubOp:
    return _StubOp(target=_StubAddr(path=tuple(levels)))


def test_ee_mentioned_labels_extracts_inflected_and_superscript_forms() -> None:
    """Estonian inflected reference forms normalise to bare/underscore labels."""
    m = mentioned_labels(
        "1) paragrahvi 16 lõike 1 punkt 5 muudetakse ja sõnastatakse järgmiselt: „5) x”;"
    )
    assert m == {"section": ["16"], "subsection": ["1"], "item": ["5"]}

    # Superscript section number folds the same way the EE parser folds it.
    assert mentioned_labels("paragrahvi 12¹ lõiget 3 muudetakse")["section"] == ["12_1"]


def test_ee_mentioned_labels_expands_coordinated_plural() -> None:
    """Coordinated ``lõigetega 4 ja 5`` yields both subsection labels."""
    m = mentioned_labels(
        "paragrahvi 160 täiendatakse lõigetega 4 ja 5 järgmises sõnastuses: „...”"
    )
    assert m["section"] == ["160"]
    assert m["subsection"] == ["4", "5"]


def test_ee_produced_labels_collects_target_and_destination() -> None:
    ops = [
        _StubOp(
            target=_StubAddr(path=(("section", "16"), ("subsection", "1"))),
            destination=_StubAddr(path=(("section", "20"),)),
        )
    ]
    produced = produced_labels(ops)
    assert produced["section"] == {"16", "20"}
    assert produced["subsection"] == {"1"}


def test_ee_clean_item_when_target_is_produced() -> None:
    """A verb-item whose named target a produced op covers is CLEAN (no drop)."""
    item = "1) paragrahvi 16 lõike 1 punkt 5 muudetakse ja sõnastatakse järgmiselt: „5) x”;"
    ops = [_section(("section", "16"), ("subsection", "1"), ("item", "5"))]
    cov = audit_amendment_labels([item], ops, sid="ee/clean")
    assert cov.n_verb_items == 1
    assert cov.n_clean_items == 1
    assert cov.drops == ()


def test_ee_unmatched_section_drop() -> None:
    """A named item no op targets, in an amendment that DID produce ops, drops."""
    items = [
        "1) paragrahvi 16 lõike 1 punkt 5 muudetakse ja sõnastatakse järgmiselt: „5) x”;",
        "2) paragrahvi 99 lõike 1 punkt 7 tunnistatakse kehtetuks;",
    ]
    # Only §16 produced; §99 item 7 is silently dropped.
    ops = [_section(("section", "16"), ("subsection", "1"), ("item", "5"))]
    cov = audit_amendment_labels(items, ops, sid="ee/unmatched")
    assert cov.n_verb_items == 2
    assert cov.n_clean_items == 1
    assert len(cov.drops) == 1
    drop = cov.drops[0]
    assert drop.tier == TIER_UNMATCHED_SECTION
    assert drop.level == "item"
    assert drop.label == "7"
    assert drop.shape == ("section", "subsection", "item")


def test_ee_coordinated_plural_partial_drop() -> None:
    """In ``punktid 5 ja 7``, only the unmatched member (7) is reported."""
    item = "1) paragrahvi 16 lõike 1 punktid 5 ja 7 tunnistatakse kehtetuks;"
    ops = [_section(("section", "16"), ("subsection", "1"), ("item", "5"))]
    cov = audit_amendment_labels([item], ops, sid="ee/coord")
    assert cov.n_clean_items == 0
    assert {d.label for d in cov.drops} == {"7"}
    assert all(d.level == "item" for d in cov.drops)


def test_ee_verb_no_op_when_amendment_produced_nothing() -> None:
    """A verb-item naming a target in an amendment with ZERO ops is verb_no_op."""
    item = "1) paragrahvi 16 lõige 1 muudetakse ja sõnastatakse järgmiselt: „(1) x”;"
    cov = audit_amendment_labels([item], [], sid="ee/noop")
    assert cov.n_verb_items == 1
    assert len(cov.drops) == 1
    assert cov.drops[0].tier == TIER_VERB_NO_OP


def test_ee_citation_context_item_is_not_a_unit() -> None:
    """Decree-body ``... § 22 lõike 2 punkti 8 alusel lisatakse ...`` is excluded."""
    item = (
        "Diplomaatilise passi taotlemisel isikut tõendavate dokumentide seaduse "
        "§ 22 lõike 2 punkti 8 alusel lisatakse taotlusele välislähetuskäskkiri."
    )
    cov = audit_amendment_labels([item], [], sid="ee/cite")
    # The citation frame removes it from the verb-item universe entirely, so it
    # produces neither a clean unit nor a drop.
    assert cov.n_verb_items == 0
    assert cov.drops == ()


def test_ee_non_verb_item_ignored() -> None:
    """A plain body sentence with no amendment verb contributes no unit."""
    cov = audit_amendment_labels(
        ["Registri pidamise eesmärgiks on teabe koondamine."], [], sid="ee/body"
    )
    assert cov.n_verb_items == 0
    assert cov.drops == ()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

@dataclass
class _Args:
    jurisdiction: str
    limit: int = 0
    workers: int = 0
    top: int = 20
    json: bool = False


def test_parse_bench_no_se_still_hit_pointer_guard(capsys) -> None:
    """`-j no parse-bench` still prints the pointer guard (no lowering adapter)."""
    from lawvm.tools.parse_bench import main

    main(_Args(jurisdiction="no"))
    out = capsys.readouterr().out
    assert "nor a lowering adapter yet" in out
    assert "no" in out


def test_parse_bench_lowering_jurisdictions_no_longer_hit_guard(monkeypatch) -> None:
    """`-j us/nz/uk parse-bench` dispatches to a lowering adapter, not the guard.

    Each adapter is stubbed so the dispatch is corpus-independent: dispatch must
    route us->run_us, nz->run_nz, uk->run_uk and never fall through to the
    structured-jurisdiction pointer guard.
    """
    import lawvm.tools.parse_bench as pb

    called: dict[str, bool] = {}
    monkeypatch.setattr(pb, "run_us", lambda args: called.__setitem__("us", True))
    monkeypatch.setattr(pb, "run_nz", lambda args: called.__setitem__("nz", True))
    monkeypatch.setattr(pb, "run_uk", lambda args: called.__setitem__("uk", True))

    for jur in ("us", "nz", "uk"):
        pb.main(_Args(jurisdiction=jur))
    assert called == {"us": True, "nz": True, "uk": True}


# ---------------------------------------------------------------------------
# Lowering-coverage adapters (archive-gated end-to-end JSON smoke)
# ---------------------------------------------------------------------------

def _canonical_data_available(farchive_name: str) -> bool:
    import os
    from pathlib import Path

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / farchive_name).exists()


def _run_lowering_json(jurisdiction: str, capsys, *, limit: int) -> dict:
    import json as _json

    from lawvm.tools.parse_bench import main

    main(_Args(jurisdiction=jurisdiction, limit=limit, workers=1, json=True))
    out = capsys.readouterr().out.strip().splitlines()
    return _json.loads(out[-1])


def _assert_lowering_payload(payload: dict, jurisdiction: str) -> None:
    assert payload["metric"] == "lowering_coverage"
    assert payload["jurisdiction"] == jurisdiction
    pct = payload["lowering_coverage_pct"]
    assert isinstance(pct, (int, float))
    assert 0.0 <= float(pct) <= 100.0
    # The worklist structure is always present (possibly empty on a tiny sample).
    assert "top_shapes" in payload
    assert isinstance(payload["top_shapes"], list)


@pytest.mark.skipif(
    not _canonical_data_available("us_federal.farchive"),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_us_lowering_coverage_smoke(capsys) -> None:
    payload = _run_lowering_json("us", capsys, limit=30)
    _assert_lowering_payload(payload, "us")
    assert payload["unit"] == "amendatory_instruction"


@pytest.mark.skipif(
    not _canonical_data_available("nz_legislation.farchive"),
    reason="canonical nz_legislation.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_nz_lowering_coverage_smoke(capsys) -> None:
    payload = _run_lowering_json("nz", capsys, limit=30)
    _assert_lowering_payload(payload, "nz")
    assert payload["unit"] == "effect_row"


@pytest.mark.skipif(
    not _canonical_data_available("uk_legislation.farchive"),
    reason="canonical uk_legislation.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_uk_lowering_coverage_smoke(capsys) -> None:
    payload = _run_lowering_json("uk", capsys, limit=10)
    _assert_lowering_payload(payload, "uk")
    assert payload["unit"] == "effect_record"
    # UK is the bounded/sampled adapter and must say so.
    assert payload["bounded_sample"] is True
    assert "bounded/sampled run" in payload["cost_note"]
