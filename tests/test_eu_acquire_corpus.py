"""Tests for the EU full-corpus acquisition CLI driver (acquire_corpus).

Offline-deterministic core: a fake enumeration snapshot + fake ``_acquire_celex``
/``_acquire_closure`` seams make the multi-language loop, owned truncation,
resume-skip, dry-run, typed-gap recording, and the closed-world accounting
hermetic (NO network).

One opt-in networked smoke test (skippable) runs ``--limit 3 --language eng``
against the live CELLAR registry and asserts the farchive grew + the summary is
well-formed, skipping cleanly with a typed witness if REST is down.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

import io
import json

from lawvm.eu import eu_acquire
from lawvm.eu.acquire_corpus import (
    CorpusAcquireRun,
    CorpusGap,
    _parse_languages,
    _print_summary,
    _write_report,
    main,
    run_corpus_acquisition,
)
from lawvm.eu.eu_enumerate import EnumerationQuery, EnumerationSnapshot

FETCHED_AT = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)

# A snapshot whose CELEX set mixes well-formed acts with a corrigendum (a real
# registry member that is NOT a well-formed act CELEX → recorded as non-act).
_ACTS = ("32016R0044", "32016R0679", "32017R0488")
_CORRIGENDUM = "32016R0044R(01)"


def _snapshot(celexes: tuple[str, ...]) -> EnumerationSnapshot:
    q = EnumerationQuery()
    return EnumerationSnapshot(
        endpoint="http://example.test/sparql",
        query_text=q.sparql_text(),
        snapshot_date="2026-06-30",
        resource_type_uri=q.resource_type_uri,
        in_force=q.in_force,
        celexes=celexes,
    )


class _FakeStateSpan:
    def __init__(self, digest: str = "deadbeef") -> None:
        self.digest = digest


class _FakeFarchive:
    """Minimal farchive double: records stores + a controllable present-set."""

    def __init__(self, present: set[str] | None = None) -> None:
        self.stored: dict[str, bytes] = {}
        self.present = present or set()

    def store(self, locator, data, *, storage_class, metadata, observed_at):
        self.stored[locator] = data
        self.present.add(locator)

    def observe(self, locator, digest, *, observed_at):
        pass

    def history(self, locator):
        return [_FakeStateSpan()] if locator in self.present else []

    def close(self):
        pass


def _fake_acquire_factory(
    *,
    added_langs: set[str] | None = None,
    fail_celexes: set[str] | None = None,
    raise_celexes: set[str] | None = None,
    missing_manifestation: set[tuple[str, str]] | None = None,
):
    """Build a fake ``acquire_celex`` seam with controllable per-call outcomes."""
    added_langs = added_langs if added_langs is not None else {"eng", "fin"}
    fail_celexes = fail_celexes or set()
    raise_celexes = raise_celexes or set()
    missing_manifestation = missing_manifestation or set()

    def _fake(celex, *, fetched_at, language, fmt, farchive, universe, **_kw):
        if celex in raise_celexes:
            raise RuntimeError(f"simulated REST 502 for {celex}")
        run = eu_acquire.CelexIngestRun(
            celex=celex,
            consolidation_date="enacted",
            expression_language=language,
            fetched_at=fetched_at,
            farchive_path="(test)",
            universe=universe,
        )
        if (celex, language) in missing_manifestation:
            run.failures.append(
                eu_acquire.CelexAcquisitionFailure(
                    rule_id="EU_ACQ.NO_MANIFESTATION",
                    phase="acquisition",
                    family="source_pathology",
                    celex=celex,
                    expression_language=language,
                    fmt=fmt,
                    locator=eu_acquire.celex_locator(celex, "enacted", language, fmt),
                    reason=f"no {fmt} manifestation for {language}",
                    detail="missing",
                    strict_disposition="abort",
                )
            )
            run.failed = 1
            return run
        if celex in fail_celexes:
            run.failures.append(
                eu_acquire.CelexAcquisitionFailure(
                    rule_id="EU_ACQ.ITEM_FETCH_FAILED",
                    phase="acquisition",
                    family="transport_cleanup",
                    celex=celex,
                    expression_language=language,
                    fmt=fmt,
                    locator=eu_acquire.celex_locator(celex, "enacted", language, fmt),
                    reason="Cellar manifestation-item fetch failed",
                    detail="HTTP 502",
                    strict_disposition="abort",
                )
            )
            run.failed = 1
            return run
        if language in added_langs:
            run.added = 2  # notice + item
            # mark the item locator present so a later --resume run sees it
            farchive.present.add(
                eu_acquire.celex_locator(celex, "enacted", language, fmt)
            )
        return run

    return _fake


# --------------------------------------------------------------------------- #
# Multi-language loop
# --------------------------------------------------------------------------- #


def test_multi_language_fetches_each_language() -> None:
    fa = _FakeFarchive()
    fake = _fake_acquire_factory(added_langs={"eng", "fin"})
    run = run_corpus_acquisition(
        farchive=fa,
        snapshot=_snapshot(_ACTS),
        fetched_at=FETCHED_AT,
        languages=("eng", "fin"),
        fmt="fmx4",
        with_closure=False,
        sample_limit=None,
        resume=False,
        dry_run=False,
        _acquire_celex=fake,
    )
    # Each of 3 acts acquired in BOTH languages.
    assert run.acquired_per_language == {"eng": 3, "fin": 3}
    assert run.acquired_total == 6
    assert run.acquisition_sampled is False


# --------------------------------------------------------------------------- #
# Owned truncation
# --------------------------------------------------------------------------- #


def test_limit_truncates_with_owned_sampled_flag() -> None:
    fa = _FakeFarchive()
    fake = _fake_acquire_factory(added_langs={"eng"})
    run = run_corpus_acquisition(
        farchive=fa,
        snapshot=_snapshot(_ACTS),
        fetched_at=FETCHED_AT,
        languages=("eng",),
        fmt="fmx4",
        with_closure=False,
        sample_limit=2,
        resume=False,
        dry_run=False,
        _acquire_celex=fake,
    )
    assert run.acquisition_sampled is True
    assert run.sample_limit == 2
    assert len(run.window_celexes) == 2
    assert run.acquired_per_language == {"eng": 2}


# --------------------------------------------------------------------------- #
# Resume
# --------------------------------------------------------------------------- #


def test_resume_skips_present_locators() -> None:
    # Pre-seed the farchive with the eng item locator for the first act.
    present_locator = eu_acquire.celex_locator(_ACTS[0], "enacted", "eng", "fmx4")
    fa = _FakeFarchive(present={present_locator})
    fake = _fake_acquire_factory(added_langs={"eng"})
    run = run_corpus_acquisition(
        farchive=fa,
        snapshot=_snapshot(_ACTS),
        fetched_at=FETCHED_AT,
        languages=("eng",),
        fmt="fmx4",
        with_closure=False,
        sample_limit=None,
        resume=True,
        dry_run=False,
        _acquire_celex=fake,
    )
    assert run.resumed_skipped_per_language == {"eng": 1}
    # The other two acts were acquired; the present one was skipped.
    assert run.acquired_per_language == {"eng": 2}


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #


def test_dry_run_fetches_nothing() -> None:
    fa = _FakeFarchive()
    calls: list[str] = []

    def _spy(celex, **_kw):  # pragma: no cover - must never be called
        calls.append(celex)
        raise AssertionError("dry-run must not fetch")

    run = run_corpus_acquisition(
        farchive=fa,
        snapshot=_snapshot(_ACTS),
        fetched_at=FETCHED_AT,
        languages=("eng", "fin"),
        fmt="fmx4",
        with_closure=True,
        sample_limit=None,
        resume=False,
        dry_run=True,
        _acquire_celex=_spy,
    )
    assert calls == []
    assert run.dry_run is True
    assert run.acquired_total == 0
    # The universe count is still reported.
    assert run.enumerated_count == len(_ACTS)
    assert run.acquirable_count == len(_ACTS)
    # The snapshot witness was still stored.
    assert run.snapshot_locator in fa.stored


# --------------------------------------------------------------------------- #
# Typed gap recording + loop continues
# --------------------------------------------------------------------------- #


def test_raising_celex_recorded_as_typed_gap_and_loop_continues() -> None:
    fa = _FakeFarchive()
    fake = _fake_acquire_factory(added_langs={"eng"}, raise_celexes={_ACTS[1]})
    run = run_corpus_acquisition(
        farchive=fa,
        snapshot=_snapshot(_ACTS),
        fetched_at=FETCHED_AT,
        languages=("eng",),
        fmt="fmx4",
        with_closure=False,
        sample_limit=None,
        resume=False,
        dry_run=False,
        _acquire_celex=fake,
    )
    # The middle act raised → recorded as a typed gap; the loop continued and
    # acquired the other two.
    assert run.acquired_per_language == {"eng": 2}
    assert any(
        g.rule_id == "EU_CORPUS.ACQUIRE_RAISED" and g.celex == _ACTS[1]
        for g in run.gaps
    )
    assert _ACTS[1] in run.failed_celexes


def test_inrun_failure_recorded_as_per_language_gap() -> None:
    fa = _FakeFarchive()
    # The first act's eng manifestation is missing (per-language gap), but fin
    # succeeds → not a whole-act failure.
    fake = _fake_acquire_factory(
        added_langs={"eng", "fin"},
        missing_manifestation={(_ACTS[0], "eng")},
    )
    run = run_corpus_acquisition(
        farchive=fa,
        snapshot=_snapshot(_ACTS),
        fetched_at=FETCHED_AT,
        languages=("eng", "fin"),
        fmt="fmx4",
        with_closure=False,
        sample_limit=None,
        resume=False,
        dry_run=False,
        _acquire_celex=fake,
    )
    # eng acquired only 2 (the first was a missing-manifestation gap); fin all 3.
    assert run.acquired_per_language == {"eng": 2, "fin": 3}
    assert any(
        g.rule_id == "EU_ACQ.NO_MANIFESTATION"
        and g.celex == _ACTS[0]
        and g.language == "eng"
        for g in run.gaps
    )


# --------------------------------------------------------------------------- #
# Closed-world accounting + non-act partition
# --------------------------------------------------------------------------- #


def test_summary_accounts_every_enumerated_id() -> None:
    fa = _FakeFarchive()
    celexes = (*_ACTS, _CORRIGENDUM)
    # One act fails on both languages; one act raises.
    fake = _fake_acquire_factory(
        added_langs={"eng"},
        fail_celexes={_ACTS[2]},
        raise_celexes={_ACTS[1]},
    )
    run = run_corpus_acquisition(
        farchive=fa,
        snapshot=_snapshot(celexes),
        fetched_at=FETCHED_AT,
        languages=("eng",),
        fmt="fmx4",
        with_closure=False,
        sample_limit=None,
        resume=False,
        dry_run=False,
        _acquire_celex=fake,
    )
    # The corrigendum is a non-act registry member, never acquired, never dropped.
    assert run.non_act_skipped == (_CORRIGENDUM,)
    # Every enumerated id is acquired, failed, or non-act (closed-world account).
    acquired_ids = {
        _ACTS[0],
    }
    failed_ids = set(run.failed_celexes)
    non_act_ids = set(run.non_act_skipped)
    enumerated_ids = set(celexes)
    assert acquired_ids | failed_ids | non_act_ids == enumerated_ids
    assert acquired_ids.isdisjoint(failed_ids)


def test_parse_languages_multi_and_comma() -> None:
    assert _parse_languages(["eng", "fin"]) == ("eng", "fin")
    assert _parse_languages(["eng,fin"]) == ("eng", "fin")
    assert _parse_languages(["eng", "eng,fin", "fin"]) == ("eng", "fin")
    assert _parse_languages(None) == ()


# --------------------------------------------------------------------------- #
# Closure
# --------------------------------------------------------------------------- #


def test_with_closure_records_closure_acts() -> None:
    fa = _FakeFarchive()
    fake = _fake_acquire_factory(added_langs={"eng"})

    class _FakeClosure:
        def __init__(self, base):
            self.acquired_celexes = [base, base + "_AMEND"]
            self.failed_celexes = []

    def _fake_closure(celex, **_kw):
        return _FakeClosure(celex)

    run = run_corpus_acquisition(
        farchive=fa,
        snapshot=_snapshot((_ACTS[0],)),
        fetched_at=FETCHED_AT,
        languages=("eng",),
        fmt="fmx4",
        with_closure=True,
        sample_limit=None,
        resume=False,
        dry_run=False,
        _acquire_celex=fake,
        _acquire_closure=_fake_closure,
    )
    assert run.closure_acts_acquired == (_ACTS[0], _ACTS[0] + "_AMEND")


# --------------------------------------------------------------------------- #
# CorpusGap typing
# --------------------------------------------------------------------------- #


def test_corpus_gap_is_typed_dict() -> None:
    gap = CorpusGap(
        celex="32016R0679",
        language="eng",
        rule_id="EU_ACQ.ITEM_FETCH_FAILED",
        reason="502",
        detail="HTTP 502",
    )
    d = gap.to_dict()
    assert d["celex"] == "32016R0679"
    assert d["rule_id"] == "EU_ACQ.ITEM_FETCH_FAILED"


# --------------------------------------------------------------------------- #
# Bounded summary output                                                       #
# --------------------------------------------------------------------------- #


def _run_with_gaps(n_gaps: int) -> CorpusAcquireRun:
    gaps = [
        CorpusGap(
            celex=f"32016R{i:04d}",
            language="eng",
            rule_id="EU_ACQ.ITEM_FETCH_FAILED",
            reason="502",
            detail="HTTP 502 " + "x" * 200,  # long detail — would bloat a one-liner
        )
        for i in range(n_gaps)
    ]
    return CorpusAcquireRun(
        snapshot_id="snap1",
        snapshot_locator="loc1",
        enumerated_count=10,
        acquirable_count=8,
        languages=("eng",),
        fmt="fmx4",
        with_closure=False,
        dry_run=False,
        acquisition_sampled=False,
        sample_limit=None,
        acquired_per_language={"eng": 5},
        gaps=gaps,
    )


def test_summary_stdout_is_bounded_and_report_holds_full_account(tmp_path) -> None:
    run = _run_with_gaps(50)
    report = tmp_path / "account.json"
    _write_report(run, report)
    out = io.StringIO()
    _print_summary(run, out=out, report_path=report, emit_json=False)
    text = out.getvalue()
    # stdout carries the human header + the gap COUNT + a pointer, never the
    # unbounded gaps list (no full JSON dump / no per-gap detail on stdout).
    assert "=== EU corpus acquisition summary ===" in text
    assert "gaps(typed)=50" in text
    assert str(report) in text
    assert "HTTP 502" not in text  # the long per-gap detail is NOT on stdout
    assert '"gaps"' not in text  # no full account JSON blob on stdout
    # The full account (embedding the gaps list) lives in the report file.
    account = json.loads(report.read_text())
    assert account["gap_count"] == 50
    assert len(account["gaps"]) == 50
    assert account["gaps"][0]["rule_id"] == "EU_ACQ.ITEM_FETCH_FAILED"


def test_summary_json_flag_restores_stdout_oneliner() -> None:
    run = _run_with_gaps(3)
    out = io.StringIO()
    _print_summary(run, out=out, report_path=None, emit_json=True)
    text = out.getvalue()
    # Legacy back-compat: the last stdout line is the full machine-readable JSON.
    last = text.strip().splitlines()[-1]
    account = json.loads(last)
    assert account["schema"] == "lawvm.eu_acquire_corpus_run.v0"
    assert account["gap_count"] == 3


# --------------------------------------------------------------------------- #
# Opt-in networked smoke (skippable)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    os.environ.get("LAWVM_EU_NETWORK_SMOKE") != "1",
    reason="opt-in networked smoke; set LAWVM_EU_NETWORK_SMOKE=1 to run",
)
def test_networked_smoke_limit_3_eng(tmp_path) -> None:
    """Live --limit 3 --language eng smoke; skips cleanly if REST is down."""
    farchive_path = tmp_path / "eu_cellar_smoke.farchive"
    argv = [
        "--farchive",
        str(farchive_path),
        "--language",
        "eng",
        "--limit",
        "3",
    ]
    # The CLI resolves --farchive through corpus_store; pass an explicit path via
    # the env override so the smoke writes into tmp_path.
    os.environ["LAWVM_FARCHIVE_DB"] = str(farchive_path)
    try:
        rc = main(argv)
    except SystemExit as exc:  # pragma: no cover - tolerate fatal SPARQL-down
        if exc.code == 2:
            pytest.skip("SPARQL enumerate down (typed FATAL witness)")
        raise
    finally:
        os.environ.pop("LAWVM_FARCHIVE_DB", None)
    # rc==2 would have come back as the return value, not an exception.
    if rc == 2:
        pytest.skip("SPARQL enumerate down (typed FATAL witness)")
    assert rc == 0
    assert farchive_path.exists()
    assert farchive_path.stat().st_size > 0
