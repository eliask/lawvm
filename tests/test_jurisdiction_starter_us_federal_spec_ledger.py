"""U.S. federal spec-discovery ledger adapter + catalog coverage.

Three layers, mirroring the US bench/dry-run test discipline (no network anywhere):

1. **Catalog coverage / anti-drift** (offline, AST): every ``us_…`` witness rule_id
   literal under ``src/lawvm/us_federal/`` has a non-empty believed_spec entry, no
   catalog key is dead, and the documented non-rule literals never sneak in as rules.

2. **Adapter logic over a synthetic report** (offline): a hand-built
   :class:`USDryRunReport` is mapped through ``us_ledger_inputs_from_reports`` to assert
   the firing / corroboration / contradiction arithmetic (agree corroborates, residual
   contradicts with the kernel's disposition, refusals fire without diverging, the
   north-star missing_source/sunset synthetics fire + diverge), plus the loud
   uncataloged-blind-spot sentinel.

3. **Real corpus over the canonical archive** (archive-gated, skipped otherwise): the
   whole adapter runs against the committed bench corpus and the canonical farchive,
   pinning the ledger shape (rules ranked, every fired rule cataloged) without brittle
   exact numbers.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from lawvm.tools.spec_ledger import SpecLedger
from lawvm.tools.spec_ledger_discovery import format_uncataloged, locate_rule_ids
from lawvm.tools.spec_ledger_us_catalog import (
    _US_RULE_SPECS,
    US_NON_RULE_LITERALS,
)
from lawvm.us_federal.bench import BenchWindow, WindowResult, WindowStatus
from dataclasses import replace

from lawvm.us_federal.dry_run import (
    DISPOSITION_LAWVM_WRONG,
    DISPOSITION_ORACLE_SUSPECT,
    US_DRY_RUN_REFUSED_TARGET_NOT_TITLE_RULE_ID,
    US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID,
    US_DRY_RUN_SECTION_AGREES_RULE_ID,
    USDryRunRefusal,
    USDryRunReport,
    USDryRunRowStatus,
    USDryRunSectionRow,
    build_us_dry_run,
)
from lawvm.us_federal.spec_ledger_adapter import (
    US_LEGACY_UNKNOWN,
    build_us_spec_ledger,
    build_us_spec_ledger_parallel,
    ledger_to_dict,
    render_text,
    us_ledger_inputs_from_reports,
)

US_DIR = Path(__file__).resolve().parents[1] / "src" / "lawvm" / "us_federal"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = REPO_ROOT / "us" / "bench" / "us_bench_corpus.csv"
FIXTURES = Path(__file__).parent / "fixtures" / "us_federal"


# ---------------------------------------------------------------------------
# 1. Catalog coverage / anti-drift (offline, AST)
# ---------------------------------------------------------------------------


# The ledger adapter CONSUMES rule ids (imports / __all__ / disposition keys); it does
# not EMIT new ones, so it is excluded from the rule-emitting-frontend discovery.
_DISCOVERY_SKIP_FILES = {"spec_ledger_adapter.py"}


def _is_rule_literal(value: str) -> bool:
    if not value.startswith("us_"):
        return False
    if value in US_NON_RULE_LITERALS:
        return False
    if "." in value:  # filenames / locator templates are never rule ids
        return False
    if ":" in value:  # f-string fragments (e.g. "us_dry_run:title") are never rule ids
        return False
    return True


def _discover_us_rule_ids() -> set[str]:
    """Every static ``us_*`` rule-id literal across the us_federal frontend, via AST."""
    found: set[str] = set()
    for path in sorted(US_DIR.glob("*.py")):
        if path.name in _DISCOVERY_SKIP_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _is_rule_literal(node.value)
            ):
                found.add(node.value)
    return found


def test_us_federal_dir_present() -> None:
    # Guard against the discovery silently finding nothing (wrong path / empty glob).
    assert US_DIR.is_dir(), US_DIR
    assert list(US_DIR.glob("*.py")), "no us_federal modules discovered"


def test_every_discovered_rule_id_is_cataloged() -> None:
    """Coverage / anti-drift: no US rule id may lack a believed_spec hypothesis."""
    discovered = _discover_us_rule_ids()
    assert discovered, "AST discovery found no US rule-id literals"
    uncataloged = sorted(discovered - set(_US_RULE_SPECS))
    locations = locate_rule_ids(US_DIR, uncataloged, repo_root=REPO_ROOT / "src")
    assert not uncataloged, (
        f"{len(uncataloged)} US witness rule id(s) have no believed_spec entry in "
        "_US_RULE_SPECS (cataloged fraction < 100%) (id <- emit site):\n"
        f"{format_uncataloged(uncataloged, locations)}"
    )


def test_no_dead_catalog_entries() -> None:
    """Every catalog key must map to a real rule-id literal in src/lawvm/us_federal/."""
    discovered = _discover_us_rule_ids()
    dead = sorted(set(_US_RULE_SPECS) - discovered)
    assert not dead, (
        f"{len(dead)} _US_RULE_SPECS key(s) do not correspond to any US rule-id literal "
        f"(stale/dead entries): {dead}"
    )


def test_all_hypotheses_non_empty() -> None:
    empty = sorted(k for k, v in _US_RULE_SPECS.items() if not v or not v.strip())
    assert not empty, f"empty believed_spec hypotheses: {empty}"


def test_excluded_non_rule_literals_are_not_cataloged() -> None:
    leaked = sorted(US_NON_RULE_LITERALS & set(_US_RULE_SPECS))
    assert not leaked, f"non-rule literals cataloged as rules: {leaked}"


# ---------------------------------------------------------------------------
# 2. Adapter logic over a synthetic report (offline)
# ---------------------------------------------------------------------------


def _window(title: int = 11, before: int = 2018, after: int = 2020) -> BenchWindow:
    return BenchWindow(
        title=title,
        before_year=before,
        after_year=after,
        include=True,
        window_law_count=1,
        prior_edition_years=(),
        note="synthetic",
    )


def _result(
    report: USDryRunReport | None, *, status: WindowStatus = WindowStatus.EVALUATED
) -> WindowResult:
    return WindowResult(
        window=_window(report.title if report else 99),
        window_status=status,
        report=report,
    )


def _base_fixture_report() -> USDryRunReport:
    """A real report from the committed Title 99 fixture window (valid boundary proof)."""
    before = (FIXTURES / "usc-dryrun-before.htm").read_bytes()
    after = (FIXTURES / "usc-dryrun-after.htm").read_bytes()
    plaw = (FIXTURES / "plaw-dryrun-strike-insert.xml").read_bytes()
    return build_us_dry_run(
        before_htm=before,
        after_htm=after,
        plaw_blobs={"PL 99-2": plaw},
        title=99,
        before_year="2023",
        after_year="2024",
    )


def _synthetic_report() -> USDryRunReport:
    """A small report exercising every adapter mapping branch.

    Built by overriding a real fixture report's section/refusal/changed-set fields (so
    the frozen boundary proof stays valid). Sections: §10 agrees; §20 a lawvm_wrong
    residual; §40 an uncataloged-rule residual (sentinel); §30 oracle-changed but never
    claimed (north-star missing_source synthetic); plus one off-title refusal.
    """
    rows = (
        USDryRunSectionRow(
            op_id="op-a",
            action="TEXT_REPLACE",
            target_address="11:10",
            section_key="11:10",
            row_status=USDryRunRowStatus.AGREE,
            rule_id=US_DRY_RUN_SECTION_AGREES_RULE_ID,
            oracle_changed=True,
        ),
        USDryRunSectionRow(
            op_id="op-b",
            action="TEXT_REPLACE",
            target_address="11:20",
            section_key="11:20",
            row_status=USDryRunRowStatus.RESIDUAL,
            rule_id=US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID,
            disposition=DISPOSITION_LAWVM_WRONG,
            oracle_changed=True,
        ),
        # An UNCATALOGED rule id must surface as a loud legacy_unknown blind spot.
        USDryRunSectionRow(
            op_id="op-c",
            action="TEXT_REPLACE",
            target_address="11:40",
            section_key="11:40",
            row_status=USDryRunRowStatus.RESIDUAL,
            rule_id="us_dry_run_residual_not_a_real_cataloged_rule",
            disposition=DISPOSITION_ORACLE_SUSPECT,
            oracle_changed=True,
        ),
    )
    refusals = (
        USDryRunRefusal(
            op_id="op-x",
            rule_id=US_DRY_RUN_REFUSED_TARGET_NOT_TITLE_RULE_ID,
            message="off-title",
        ),
    )
    return replace(
        _base_fixture_report(),
        title=11,
        before_year="2018",
        after_year="2020",
        rows=rows,
        refusals=refusals,
        # §30 is oracle-changed but never claimed -> a north-star missing_source synthetic.
        oracle_changed_sections=("11:10", "11:20", "11:30", "11:40"),
        claimed_sections=("11:10", "11:20", "11:40"),
        sunset_reversions=(),
    )


def test_agree_corroborates_residual_contradicts_refusal_only_fires() -> None:
    report = _synthetic_report()
    inputs = us_ledger_inputs_from_reports([_result(report)])
    assert len(inputs) == 1
    inp = inputs[0]
    f = inp.rule_firings

    # The agree row fires the AGREES witness once, corroborated (no divergence on it).
    assert f[US_DRY_RUN_SECTION_AGREES_RULE_ID] == 1
    # The lawvm_wrong residual fires + diverges.
    assert f[US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID] == 1
    # The off-title refusal fires but is never a divergence (coverage frontier).
    assert f[US_DRY_RUN_REFUSED_TARGET_NOT_TITLE_RULE_ID] == 1
    refusal_divergences = [
        d for d in inp.divergences if d.rule_id == US_DRY_RUN_REFUSED_TARGET_NOT_TITLE_RULE_ID
    ]
    assert refusal_divergences == []

    # The §30 missing_source synthetic fires + diverges as missing_source.
    ms = [d for d in inp.divergences if d.disposition == "missing_source"]
    assert [d.section_key for d in ms] == ["11:30"]

    # The agree row never produces a divergence.
    agree_divs = [
        d for d in inp.divergences if d.rule_id == US_DRY_RUN_SECTION_AGREES_RULE_ID
    ]
    assert agree_divs == []


def test_uncataloged_rule_is_a_loud_legacy_unknown_blind_spot() -> None:
    report = _synthetic_report()
    inputs = us_ledger_inputs_from_reports([_result(report)])
    from lawvm.tools.spec_ledger import build_ledger

    ledger = build_ledger(
        inputs,
        jurisdiction="us",
        mode="test",
        catalog=_US_RULE_SPECS,
    )
    art = ledger_to_dict(ledger)
    assert "us_dry_run_residual_not_a_real_cataloged_rule" in art["legacy_unknown_rules"]
    # Its confidence tier is the loud sentinel, not a fake "certain".
    by_id = {r["rule_id"]: r for r in art["rules"]}
    assert by_id["us_dry_run_residual_not_a_real_cataloged_rule"]["confidence"] == (
        US_LEGACY_UNKNOWN
    )
    # And the cataloged rules are flagged cataloged.
    assert by_id[US_DRY_RUN_SECTION_AGREES_RULE_ID]["cataloged"] is True


def test_skipped_result_contributes_nothing() -> None:
    skipped = _result(None, status=WindowStatus.SKIPPED)
    assert us_ledger_inputs_from_reports([skipped]) == []


# ---------------------------------------------------------------------------
# Lane B: us_confidence is fail-loud on an uncataloged rule id (no optimistic
# default-to-certain on a missing key).
# ---------------------------------------------------------------------------


def test_us_confidence_certain_is_the_cataloged_non_heuristic_complement() -> None:
    """A cataloged, non-heuristic rule resolves to ``certain`` (behaviour preserved)."""
    from lawvm.tools.spec_ledger_us_catalog import (
        US_CONFIDENCE_CERTAIN,
        US_CONFIDENCE_HEURISTIC,
        us_confidence,
    )

    # An explicitly-heuristic cataloged rule stays heuristic.
    assert us_confidence("us_amend_strike_insert") == US_CONFIDENCE_HEURISTIC
    # A cataloged rule with no heuristic registration is certain by complement.
    assert us_confidence(US_DRY_RUN_SECTION_AGREES_RULE_ID) == US_CONFIDENCE_CERTAIN


def test_us_confidence_fails_loud_on_uncataloged_rule() -> None:
    """An uncataloged rule id must NOT silently default to the most-confident tier.

    The optimistic-default-on-miss trap: previously a rule id absent from the
    confidence map returned ``certain``. Now an id absent from BOTH the heuristic
    map and the believed-spec catalog raises a distinct named error so a typo /
    never-classified rule cannot masquerade as a maximum-confidence fact.
    """
    from lawvm.tools.spec_ledger_us_catalog import (
        USConfidenceClassificationError,
        us_confidence,
    )

    with pytest.raises(USConfidenceClassificationError, match="refusing to default"):
        us_confidence("us_a_brand_new_rule_nobody_classified")


def test_every_cataloged_us_rule_resolves_without_raising() -> None:
    """Self-evidencing completeness: every cataloged rule has a confidence tier.

    This is the dual-registration completeness gate for the US confidence split:
    the heuristic map and the certain-complement together must classify every
    believed-spec rule, with no cataloged rule falling through to the fail-loud
    raise.
    """
    from lawvm.tools.spec_ledger_us_catalog import (
        US_CONFIDENCE_CERTAIN,
        US_CONFIDENCE_HEURISTIC,
        us_confidence,
    )

    for rid in _US_RULE_SPECS:
        tier = us_confidence(rid)
        assert tier in {US_CONFIDENCE_CERTAIN, US_CONFIDENCE_HEURISTIC}, (rid, tier)


def test_no_dead_heuristic_confidence_entries() -> None:
    """Every heuristic-registered rule must be cataloged (no stale confidence keys)."""
    from lawvm.tools.spec_ledger_us_catalog import (
        _US_RULE_CONFIDENCE,
    )

    dead = sorted(set(_US_RULE_CONFIDENCE) - set(_US_RULE_SPECS))
    assert not dead, (
        f"{len(dead)} _US_RULE_CONFIDENCE key(s) are not in _US_RULE_SPECS "
        f"(stale heuristic registrations): {dead}"
    )


# ---------------------------------------------------------------------------
# 3. Real corpus over the canonical archive (archive-gated, no network)
# ---------------------------------------------------------------------------


def _canonical_archive_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "us_federal.farchive").exists()


# Opt-in env flag to run the WHOLE corpus through the SERIAL builder in a single
# process (the historical path). The default real-corpus test below runs the full
# corpus through the PARALLEL builder (byte-identical ledger, same assertions) so the
# shard stays fast; this flag exists only for an explicit single-process reproduction.
_FULL_CORPUS_SERIAL = os.environ.get("LAWVM_US_FULL_CORPUS_TEST") == "1"

# Worker count for the parallelized real-corpus ledger build. Bounded so the test
# stays well under the WSL2 memory ceiling (each worker holds an open farchive handle).
_SPEC_LEDGER_TEST_WORKERS = min(8, (os.cpu_count() or 2))


@pytest.mark.skipif(
    not _canonical_archive_available(),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_real_corpus_builds_a_ranked_ledger_with_every_fired_rule_cataloged() -> None:
    """Full real corpus, ranked + every-fired-rule-cataloged — parallelized.

    The POINT of this test is the ledger SHAPE over the *whole* real corpus (rules
    ranked contradicted-desc, no legacy_unknown blind spots, the amendatory family
    visible). Coverage is therefore NOT sampled: every included window is evaluated.
    Only the EXECUTION MODEL changes — the windows are sharded across worker processes
    via :func:`build_us_spec_ledger_parallel`, whose determinism contract makes the
    resulting ledger byte-identical to the serial builder (pinned by the dedicated
    parallel-vs-serial test below). The corpus grew to 175+ windows (the title-42
    ACA-era window alone composes 800+ Public Laws); serial evaluation was ~15-20 min.
    Set ``LAWVM_US_FULL_CORPUS_TEST=1`` to run the full sweep through the single-process
    serial builder instead.
    """
    from lawvm.us_federal.bench import load_corpus
    from lawvm.us_federal.sources import open_us_federal_farchive

    windows = load_corpus(DEFAULT_CORPUS)
    if _FULL_CORPUS_SERIAL:
        archive = open_us_federal_farchive(readonly=True)
        try:
            ledger = build_us_spec_ledger(archive, windows)
        finally:
            archive.close()
    else:
        ledger = build_us_spec_ledger_parallel(
            windows, workers=_SPEC_LEDGER_TEST_WORKERS
        )

    assert isinstance(ledger, SpecLedger)
    assert ledger.jurisdiction == "us"
    assert ledger.statutes >= 1  # at least one window evaluates
    assert ledger.rules, "the ledger must carry at least one fired rule"

    art = ledger_to_dict(ledger)
    # Every fired rule across the real corpus must be cataloged: no legacy_unknown
    # blind spots (the catalog is the keepable asset; a miss is a loud failure).
    assert art["legacy_unknown_rules"] == [], (
        f"uncataloged fired US rules: {art['legacy_unknown_rules']}"
    )

    # The AGREES witness fires (coverage > 0) and is corroborated, never contradicted.
    by_id = {r["rule_id"]: r for r in art["rules"]}
    agrees = by_id[US_DRY_RUN_SECTION_AGREES_RULE_ID]
    assert agrees["firings"] >= 1
    assert agrees["contradicted"] == 0

    # Rules are ranked contradicted-desc: the first row's contradicted is the max.
    contradicted = [r["contradicted"] for r in art["rules"]]
    assert contradicted == sorted(contradicted, reverse=True)

    # The text-mismatch residual is the most-contradicted lowering hypothesis (the
    # highest-value fix target). Pin it as a non-brittle, monotone-ish floor.
    assert by_id[US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID]["contradicted"] >= 1

    # The amendatory lowering family is visible (firings from compiled ops), so the
    # rules are not invisible blind spots.
    assert by_id["us_amend_strike_insert"]["firings"] >= 1

    # render_text never crashes and surfaces the headline.
    text = render_text(ledger)
    assert "US discovered-spec ledger" in text


@pytest.mark.skipif(
    not _canonical_archive_available(),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_parallel_spec_ledger_is_byte_identical_to_serial() -> None:
    """The parallel ledger builder reproduces the serial builder exactly.

    Determinism contract: sharding the windows across worker processes (each with its
    own read-only farchive handle) and reassembling the neutral per-window inputs in
    corpus order, then running the SAME ``build_ledger`` pass on the parent, yields a
    ledger whose ``ledger_to_dict`` JSON view is byte-identical to the serial builder's
    on the same windows — same firings, same dispositions, same ranking, same exemplars.

    This is what licenses the full-corpus test above to use the parallel builder: a
    fast, fixed, multi-window sub-corpus (the always-present Title 11 windows plus a
    second title so multi-shard reassembly across titles is exercised) pins the
    serial==parallel equivalence without paying the full-corpus cost.
    """
    from lawvm.us_federal.bench import load_corpus
    from lawvm.us_federal.sources import open_us_federal_farchive

    all_windows = load_corpus(DEFAULT_CORPUS)
    # A small but multi-title, multi-window sub-corpus: enough to exercise sharding
    # across >1 worker and cross-title reassembly, cheap enough to stay fast.
    title11 = [w for w in all_windows if w.title == 11]
    title10 = [w for w in all_windows if w.title == 10][:2]
    excluded = [w for w in all_windows if not w.include][:1]
    sub_corpus = title11 + title10 + excluded
    assert len([w for w in sub_corpus if w.include]) >= 2, (
        "need >=2 included windows to exercise sharding"
    )

    archive = open_us_federal_farchive(readonly=True)
    try:
        serial = build_us_spec_ledger(archive, sub_corpus)
    finally:
        archive.close()

    parallel = build_us_spec_ledger_parallel(sub_corpus, workers=2)

    # Byte-identical JSON projection of the whole ledger (firings, dispositions,
    # ranking, exemplars, legacy_unknown set, statute/error counts).
    import json

    serial_json = json.dumps(ledger_to_dict(serial), sort_keys=True)
    parallel_json = json.dumps(ledger_to_dict(parallel), sort_keys=True)
    assert parallel_json == serial_json

    # And the headline render is identical too (exemplar ordering is corpus-stable).
    assert render_text(parallel) == render_text(serial)
    assert parallel.statutes == serial.statutes
    assert parallel.statute_errors == serial.statute_errors
