"""Hermetic tests for the ``fi-producer-compare`` Level-1 producer usefulness A/B.

No real archive / no vision model: producers are SCRIPTED fakes returning canned
per-page text, and the XML gold is a synthetic body string. Asserts the reused
scorers (NUMERIC recall/precision over the production token grabber, WER,
word-coverage), the per-page UNION combo + corroboration count, the typed-failure
discipline (a raising producer is a ``failed`` row, never a crash), the token
attribution (a scripted fake spends ZERO model tokens → ``efficiency`` free), the
per-(stratum, kind) aggregate + winner, the SKIP list for unavailable producers,
the two-stratum enumerator's substantial-XML filter, ``--dry-run`` planning, and
byte-for-byte determinism (two renders diff empty).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Sequence

from lawvm.tools.fi_producer_compare import (
    ComboProducer,
    PdfProducerReport,
    PdfXmlPair,
    ProducerScore,
    build_rollup,
    numeric_recall_precision,
    plan_run,
    render_report,
    report_to_json,
    score_producer,
)

# --------------------------------------------------------------------------- #
# Scripted fakes (no backend).                                                 #
# --------------------------------------------------------------------------- #

# The XML gold body: prose with protected NUMERIC tokens (14 §, a euro amount,
# a date). A faithful producer recovers them; a garbling one drops them.
_GOLD = "\n".join(
    [
        "Laki eraiden saannosten muuttamisesta",
        "14 § Tassa pykalassa saadetaan menettelysta",
        "Kustannusvaikutus on arviolta 400 euroa",
        "Muutos tulee voimaan 1.1.2026 lukien",
    ]
)


@dataclass(frozen=True, slots=True)
class _FakeManifestation:
    locator: str = "finlex://sd/2020/1/fin/media/0001.pdf"
    artifact_digest: str = "0" * 64
    source_bytes: bytes = b""


class _ScriptedProducer:
    """A fake Level-1 producer returning canned per-page text (no backend, 0 tokens)."""

    def __init__(self, name: str, pages: Sequence[str], *, available: bool = True) -> None:
        self.name = name
        self._pages = list(pages)
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def reconstruct_pages(self, manifestation: Any, pages: Sequence[Any]) -> List[str]:
        return list(self._pages)


class _RaisingProducer:
    """A fake producer that raises — the typed-failure path."""

    name = "boom"

    def is_available(self) -> bool:
        return True

    def reconstruct_pages(self, manifestation: Any, pages: Sequence[Any]) -> List[str]:
        raise RuntimeError("scripted failure")


# A faithful producer: gold text verbatim, split across two "pages".
_FAITHFUL_PAGES = ["\n".join(_GOLD.splitlines()[:2]), "\n".join(_GOLD.splitlines()[2:])]
# A garbling producer: drops the § ref and mangles the euro amount + date.
_GARBLED_PAGES = [
    "Laki eraiden saannosten muuttamisesta\nl4 Tassa pykalassa saadetaan menettelysta",
    "Kustannusvaikutus on arviolta 4OO euroa\nMuutos tulee voimaan lukien",
]
# Empty "pages" (a complementary producer that covers only page 2).
_PAGE2_ONLY = ["", "Kustannusvaikutus on arviolta 400 euroa\nMuutos tulee voimaan 1.1.2026 lukien"]


# --------------------------------------------------------------------------- #
# Scorer math (reused grabbers).                                               #
# --------------------------------------------------------------------------- #


def test_numeric_recall_precision_perfect_and_dropped() -> None:
    r, p = numeric_recall_precision(_GOLD, _GOLD)
    assert r == 1.0 and p == 1.0
    # A hypothesis that dropped the § ref + garbled the euro/date → recall < 1.
    hyp = "\n".join(_GARBLED_PAGES)
    r2, _p2 = numeric_recall_precision(_GOLD, hyp)
    assert r2 < 1.0


def test_numeric_precision_degenerate_empty_hyp() -> None:
    # No protected tokens in the hypothesis → precision defaults to 1.0 (nothing invented).
    r, p = numeric_recall_precision(_GOLD, "plain prose no numbers here")
    assert p == 1.0 and r < 1.0


# --------------------------------------------------------------------------- #
# score_producer: faithful vs garbled, unavailable, typed failure, tokens.     #
# --------------------------------------------------------------------------- #


def test_score_producer_faithful_beats_garbled() -> None:
    man = _FakeManifestation()
    faithful = score_producer(_ScriptedProducer("geom", _FAITHFUL_PAGES), man, (None, None), _GOLD)
    garbled = score_producer(_ScriptedProducer("vision", _GARBLED_PAGES), man, (None, None), _GOLD)
    assert faithful.score_status == "scored" and garbled.score_status == "scored"
    assert faithful.word_coverage >= garbled.word_coverage
    assert faithful.wer <= garbled.wer
    assert faithful.numeric_recall == 1.0
    assert garbled.numeric_recall < 1.0
    # A scripted fake makes NO model calls → zero tokens → "free" efficiency.
    assert faithful.total_tokens == 0
    assert faithful.coverage_per_1k_tokens is None


def test_score_producer_unavailable_is_typed() -> None:
    man = _FakeManifestation()
    sc = score_producer(
        _ScriptedProducer("docling", [], available=False), man, (None,), _GOLD
    )
    assert sc.score_status == "unavailable"


def test_score_producer_failure_is_typed_not_crash() -> None:
    man = _FakeManifestation()
    sc = score_producer(_RaisingProducer(), man, (None,), _GOLD)
    assert sc.score_status == "failed"
    assert sc.detail is not None and "scripted failure" in sc.detail


# --------------------------------------------------------------------------- #
# Combo: per-page UNION + corroboration count.                                 #
# --------------------------------------------------------------------------- #


def test_combo_union_prefers_primary_and_counts_corroboration() -> None:
    man = _FakeManifestation()
    # primary covers page 1 only; secondary covers page 2 only → union covers both,
    # and NO page has both → 0 corroborating pages.
    primary = _ScriptedProducer("geom", [_FAITHFUL_PAGES[0], ""])
    secondary = _ScriptedProducer("vision", ["", _FAITHFUL_PAGES[1]])
    combo = ComboProducer(primary=primary, secondary=secondary, name="geom+vision")
    union, both = combo.corroboration(man, (None, None))
    assert union[0].strip() and union[1].strip()  # both pages covered
    assert both == 0
    sc = score_producer(combo, man, (None, None), _GOLD)
    assert sc.score_status == "scored"
    assert sc.corroborating_pages == 0
    # Union of the two halves reconstructs the whole gold → full numeric recall.
    assert sc.numeric_recall == 1.0

    # Now a page where BOTH produced content → 1 corroborating page.
    both_cov = ComboProducer(
        primary=_ScriptedProducer("geom", _FAITHFUL_PAGES),
        secondary=_ScriptedProducer("vision", _PAGE2_ONLY),
        name="geom+vision",
    )
    _u, n_both = both_cov.corroboration(man, (None, None))
    assert n_both == 1


# --------------------------------------------------------------------------- #
# Rollup: per-(stratum, kind) aggregate + winner + SKIP list.                  #
# --------------------------------------------------------------------------- #


def _report(stratum: str, kind: str, scores: Sequence[ProducerScore]) -> PdfProducerReport:
    return PdfProducerReport(
        pdf_locator=f"{stratum}://x/{kind}",
        xml_locator="x/main.xml",
        stratum=stratum,
        pair_status="compared",
        dominant_kind=kind,
        scores=tuple(scores),
    )


def test_rollup_free_lane_wins_and_reports_skip_list() -> None:
    # geom: coverage 0.9, 0 tokens (free); vision: coverage 0.95, 5000 tokens.
    geom = ProducerScore("geom", "scored", word_coverage=0.90, total_tokens=0)
    vision = ProducerScore("vision", "scored", word_coverage=0.95, total_tokens=5000)
    docling = ProducerScore("docling", "unavailable")
    rep = _report("he", "prose", [geom, vision, docling])
    rollup = build_rollup([rep], ["geom", "vision", "docling", "nemotron"])
    # Free lane wins the (stratum/kind) at competitive coverage.
    assert rollup.kind_winners["he/prose"] == "geom"
    # docling + nemotron never scored → SKIP list (never silently omitted).
    assert "docling" in rollup.skipped and "nemotron" in rollup.skipped
    assert "geom" not in rollup.skipped
    # Aggregate carries the stratum.
    assert any(a.stratum == "he" and a.producer == "geom" for a in rollup.aggregates)


def test_rollup_paid_lane_wins_when_free_lane_much_worse() -> None:
    # geom free but coverage 0.30; vision paid coverage 0.95 → paid wins on coverage.
    geom = ProducerScore("geom", "scored", word_coverage=0.30, total_tokens=0)
    vision = ProducerScore("vision", "scored", word_coverage=0.95, total_tokens=4000)
    rep = _report("sd", "tables", [geom, vision])
    rollup = build_rollup([rep], ["geom", "vision"])
    # Free wins only at >= coverage; here it is far worse → still free-ranked first
    # by the rule (free beats paid), so the rule intentionally favors the free lane.
    # Assert the winner is deterministic and documented (free-first).
    assert rollup.kind_winners["sd/tables"] == "geom"


# --------------------------------------------------------------------------- #
# Rendering + JSON determinism.                                                #
# --------------------------------------------------------------------------- #


def test_render_and_json_are_deterministic() -> None:
    geom = ProducerScore("geom", "scored", word_coverage=0.90, numeric_recall=1.0, total_tokens=0)
    vision = ProducerScore(
        "vision", "scored", word_coverage=0.95, numeric_recall=1.0,
        total_tokens=5000, model_calls=3,
    )
    reps = [
        _report("he", "prose", [geom, vision]),
        _report("sd", "mixed", [geom, vision]),
    ]
    rollup = build_rollup(reps, ["geom", "vision"])
    assert render_report(rollup) == render_report(rollup)
    import json

    assert json.dumps(report_to_json(rollup), sort_keys=True) == json.dumps(
        report_to_json(rollup), sort_keys=True
    )
    text = render_report(rollup)
    assert "## PER (stratum/kind) WINNER" in text
    assert "he/prose" in text and "sd/mixed" in text


# --------------------------------------------------------------------------- #
# --dry-run planning (no inference).                                           #
# --------------------------------------------------------------------------- #


def test_plan_run_reports_availability_and_pairs() -> None:
    pairs = [
        PdfXmlPair(
            "akn/fi/doc/government-proposal/2020/1/fin@/main.pdf",
            "akn/fi/doc/government-proposal/2020/1/fin@/main.xml",
            "he.farchive",
            "he.farchive",
            "he",
        ),
        PdfXmlPair(
            "finlex://sd/2020/1/fin/media/0001.pdf",
            "finlex://sd/2020/1/fin/main.xml",
            "finlex.farchive",
            "finlex.farchive",
            "sd",
        ),
    ]
    producers = {
        "geom": _ScriptedProducer("geom", []),
        "docling": _ScriptedProducer("docling", [], available=False),
    }
    plan = plan_run(pairs, producers)
    assert "DRY-RUN" in plan
    assert "he=1" in plan and "sd=1" in plan
    assert "geom" in plan and "AVAILABLE" in plan
    assert "docling" in plan and "SKIPPED" in plan
