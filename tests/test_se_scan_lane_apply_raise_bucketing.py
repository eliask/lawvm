"""iter4 W1 C2 (silent-failure review HIGH #3) — SE scan-lane apply_raise bucketing.

Tests that ``scan_se_official_replay_act`` correctly routes the
``SE_REPLAY_OUTCOME_APPLY_RAISE`` typed outcome from
:func:`check_se_official_replay` to a top-level ``outcome == "error"`` summary
(so :mod:`lawvm.tools.se_bench` routes it to :class:`BenchStatus.CRASH`, NOT
:class:`BenchStatus.SOURCE_UNAVAILABLE`).

Pre-fix (iter3 W3): ``scan_se_official_replay_act`` collapsed ALL non-feasible
typed outcomes (``older_base_required`` / ``precondition_issues_blocking`` /
``apply_raise``) to top-level ``"outcome": "older_base_required"`` at line
~3972, so the bench comparator at ``tools/se_bench.py:90-101`` misclassified
genuine apply-fold raises as :class:`BenchStatus.SOURCE_UNAVAILABLE`
(manual-compilation frontier). That was a §2.9 worst-class silent failure: the
apply_raise guard existed but its misclassification prevented the crash from
surfacing in the aggregate.

Post-fix (iter4 W1, this commit): the scan-lane dispatcher has an explicit
``if typed_outcome == SE_REPLAY_OUTCOME_APPLY_RAISE:`` branch that returns a
top-level ``outcome == "error"`` summary with the typing §1.10
exception_type/exception/clause_text witnesses projected onto
``error_type`` / ``error_detail`` / ``clause_text``. The bench comparator's
existing ``outcome == "error"`` → :class:`BenchStatus.CRASH` lane
(``se_bench.py:113-117``) then routes it correctly; ``clause_text`` is now
surfaced as a third CRASH witness (``se_bench.py:105-112``).

Mirrors the fire-drill pattern from
``tests/test_sweden_fetch.py::test_check_se_official_replay_propagates_partial_adjudications_on_apply_raise``
(iter3 W3 — the production-caller half of this contract). This test closes the
scan-lane half: the preserved partial adjudications from iter3 W3 must survive
the scan-lane re-bucketing AND the bench comparator must classify them as a
CRASH failure, not a manual-compilation frontier state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing_extensions import override


from lawvm.core.bench_contract import BenchStatus, BenchUnitResult
from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.sweden.fetch import (
    _ArchiveLike,
    scan_se_official_replay_act,
    se_legal_operation_to_dict,
)
from lawvm.tools import se_bench


# ---------------------------------------------------------------------------
# Test fixture (mirrors tests/test_sweden_fetch.py::_FakeArchive — small local
# copy keeps this test file conflict-free from concurrent-session WIP in the
# larger test_sweden_fetch.py module).
# ---------------------------------------------------------------------------


@dataclass
class _FakeArchive(_ArchiveLike):
    fetched: dict[str, bytes] = field(default_factory=dict)
    stored: dict[str, bytes] = field(default_factory=dict)
    fetch_calls: list[tuple[str, str, float]] = field(default_factory=list)

    def fetch(
        self, url: str, max_age_hours: float = 168.0, headers: dict | None = None, content_type: str = "auto"
    ) -> bytes | None:
        self.fetch_calls.append((url, content_type, max_age_hours))
        return self.fetched.get(url)

    @override
    def store(self, locator: str, data: bytes, *, storage_class: str | None = None) -> str:
        self.stored[locator] = data
        return "fakehash"

    @override
    def get(self, locator: str) -> bytes | None:
        return self.stored.get(locator)

    def get_latest(self, locator: str) -> bytes | None:
        return self.stored.get(locator)

    @override
    def has(self, locator: str, *, max_age_hours: float = float("inf")) -> bool:
        return locator in self.stored

    def is_fresh(self, locator: str, max_age_hours: float) -> bool:
        return locator in self.stored

    def locators(self, pattern: str = "%") -> list[str]:
        return [k for k in self.stored if pattern.replace("%", "") in k]


def test_scan_se_official_replay_act_routes_apply_raise_to_crash_not_source_unavailable(
    monkeypatch,
) -> None:
    """§1.10 + §2.9 fire-drill (iter4 W1 C2 / silent-failure review HIGH #3):

    When ``apply_se_ops_conserved`` raises mid-apply,
    :func:`check_se_official_replay` returns a typed
    ``outcome='apply_raise'`` / ``reason_code='se_replay_apply_raise'`` dict
    (iter3 W3). :func:`scan_se_official_replay_act` MUST dispatch on the
    explicit ``SE_REPLAY_OUTCOME_APPLY_RAISE`` branch and return a top-level
    ``outcome == "error"`` summary (NOT collapse it to ``"older_base_required"``
    as pre-fix) — so :mod:`lawvm.tools.se_bench` routes it to
    :class:`BenchStatus.CRASH` (a genuine apply-fold failure that fails the
    bench), NOT :class:`BenchStatus.SOURCE_UNAVAILABLE` (a manual-compilation
    frontier state where the source does not deterministically specify the
    replayable base), AND carries the typed §1.10
    exception_type/exception/clause_text witnesses on the summary's
    ``error_type`` / ``error_detail`` / ``clause_text`` fields so the bench's
    CRASH witnesses surface them without re-running extraction.
    """
    base_payload = {
        "beteckning": "2026:777",
        "rubrik": "Förordning (2026:777) om test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": (
            "2 § /Upphör att gälla U:2026-04-15/\n"
            "Gammal lydelse.\n\n"
            "2 § /Träder i kraft I:2026-04-15/\n"
            "Ny lydelse. Förordning (2026:286).\n"
        ),
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:777) om test",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:777",
        "is_amending_act": True,
        "published_date": "2026-04-20",
        "issued_date": "2026-04-18",
        "enacting_clause": "Regeringen föreskriver att 2 § förordningen (2026:777) om test ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2"],
        "provisions": [{"label": "2", "text": "Ny lydelse."}],
        "signatories": [],
        "footnotes": [],
    }
    valid_op = LegalOperation(
        op_id="se_official_replace_2",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "2"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Ny lydelse."),
        source=OperationSource(statute_id="2026:286", effective="2026-04-15"),
    )
    archive = _FakeArchive(
        stored={
            "se://sfs/2026:777/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.ops.json": json.dumps(
                [se_legal_operation_to_dict(op) for op in [valid_op]],
                ensure_ascii=False,
            ).encode("utf-8"),
        }
    )

    raise_message = "synthesized mid-apply raise (e.g. se strict_action_family=True)"

    # Spy: replace ``apply_se_ops_conserved`` in the fetch module with a wrapper
    # that appends one pre-raise adjudication (mirroring bare-apply's per-op
    # skip emission BEFORE the §1.10 fail-loud raise) then raises ValueError.
    # Mirrors the spy in
    # ``test_sweden_fetch.py::test_check_se_official_replay_propagates_partial_adjudications_on_apply_raise``
    # (iter3 W3); this test drives the FULL
    # :func:`scan_se_official_replay_act` production path (§2.9
    # guard-liveness: the new explicit ``SE_REPLAY_OUTCOME_APPLY_RAISE`` branch
    # must fire from the production lane, not just from the unit-test of the
    # branch predicate).
    def spy_apply_se_ops_conserved(statute, ops, **kwargs):
        adjudications_out = kwargs.get("adjudications_out")
        if adjudications_out is not None:
            adjudications_out.append(
                CompileAdjudication(
                    kind="se_replay_target_not_found",
                    message=(
                        "Synthesized pre-raise skip adjudication — op target "
                        "not in the baseline body (mirrors bare-apply's per-op "
                        "skip emission BEFORE the §1.10 fail-loud raise)."
                    ),
                    source_statute="2026:286",
                    blocking=False,
                    phase="replay",
                    op_id="se_official_replace_2",
                    detail={
                        "rule_id": "se_replay_target_not_found",
                        "phase": "replay",
                        "blocking": False,
                    },
                )
            )
        raise ValueError(raise_message)

    monkeypatch.setattr(
        "lawvm.sweden.fetch.apply_se_ops_conserved",
        spy_apply_se_ops_conserved,
    )

    # Drive the FULL scan-lane production path: archive + monkeypatched apply
    # raise → scan_se_official_replay_act. Pre-fix this returned
    # outcome="older_base_required" + error_type="NotImplementedError"; post-fix
    # it must return outcome="error" with the typed §1.10 witnesses.
    summary = scan_se_official_replay_act(archive, "2026:286")

    # === Scan-lane routing assertions ===
    # Pre-fix: ``outcome == "older_base_required"`` (the collapsed non-feasible
    # bucket) ← HEAD pre-iter4 W1 C2 — the §2.9 worst-class silent failure the
    # bench then mis-routes to SOURCE_UNAVAILABLE.
    # Post-fix: ``outcome == "error"`` with the typed witnesses.
    assert summary["outcome"] == "error", (
        f"scan_se_official_replay_act routed apply_raise to summary outcome="
        f"{summary.get('outcome')!r}, expected 'error'. Pre-iter4-W1-C2 this "
        f"collapsed to 'older_base_required' (the manual-compilation frontier "
        f"bucket) — the §2.9 worst-class silent failure the bench then "
        f"mis-routed to BenchStatus.SOURCE_UNAVAILABLE (silent-failure review "
        f"HIGH #3). The new explicit SE_REPLAY_OUTCOME_APPLY_RAISE branch "
        f"(sweden/fetch.py:3981+) must fire from the production lane."
    )
    # The typed §1.10 witnesses are projected onto the summary's top-level
    # error_type / error_detail / clause_text fields so the bench comparator's
    # CRASH lane can surface them without re-running extraction.
    assert summary["error_type"] == "ValueError", (
        f"summary['error_type']={summary.get('error_type')!r}; expected "
        f"'ValueError' — the §1.10 exception_type from the apply_raise catch."
    )
    assert summary["error_detail"] == raise_message, (
        f"summary['error_detail']={summary.get('error_detail')!r}; expected "
        f"the synthesized raise message — the §1.10 exception string."
    )
    assert summary["clause_text"] == raise_message, (
        f"summary['clause_text']={summary.get('clause_text')!r}; expected "
        f"the raise message (the apply_raise catch currently carries "
        f"str(e)[:400] as clause_text; the per-op source_clause_extract is "
        f"deferred per task #50 — see iter4 W1 M2 comment at "
        f"sweden/fetch.py:3604+)."
    )
    # The preserved partial adjudications (pre-raise skip + apply_raise
    # orchestration) survive the scan-lane re-bucketing — §1.0
    # evidence-not-silently-destroyed contract from iter3 W3.
    assert summary.get("adjudications"), (
        "summary['adjudications'] is empty — the §1.0 partial-witness "
        "preservation from iter3 W3 regressed at the scan-lane boundary."
    )
    summary_adjudication_kinds = {a.get("kind") for a in summary["adjudications"]}
    assert "se_replay_target_not_found" in summary_adjudication_kinds, (
        "summary['adjudications'] does not carry the pre-raise "
        "se_replay_target_not_found witness — the §1.0/§1.8 partial-loss "
        "failure (the scan-lane re-bucketing dropped it)."
    )
    assert "se_replay_apply_raise" in summary_adjudication_kinds, (
        "summary['adjudications'] does not carry the typed "
        "se_replay_apply_raise orchestration adjudication — the §1.10 "
        "embed-snippet contract is unmet."
    )
    # The typed `reason_code` and `typed_outcome` are preserved on the summary
    # so downstream aggregate reporters (e.g. SEAggregateReporter) can dispatch
    # without re-parsing the adjudication ledger.
    assert summary["reason_code"] == "se_replay_apply_raise"
    assert summary["typed_outcome"] == "apply_raise"

    # === Bench comparator routing assertions ===
    # The whole point of the routing fix: a summary with `outcome="error"` and
    # the §1.10 witnesses MUST route to BenchStatus.CRASH (a genuine failure),
    # NOT BenchStatus.SOURCE_UNAVAILABLE (a manual-compilation frontier state
    # that does not fail the bench and obscures the crash in the aggregate).
    bench_result: BenchUnitResult = se_bench.se_bench_unit_result(summary)
    assert bench_result.bench_unit_status is BenchStatus.CRASH, (
        f"se_bench_unit_result routed apply_raise summary to "
        f"{bench_result.bench_unit_status!r}; expected BenchStatus.CRASH — "
        f"this is the §2.9 worst-class silent failure: a genuine apply-fold "
        f"raise mis-routed as SOURCE_UNAVAILABLE 'silent success' (silent-"
        f"failure review HIGH #3). The fix at scan_se_official_replay_act+"
        f"se_bench.py closes the misclassification."
    )
    assert bench_result.is_failure, (
        "BenchStatus.CRASH must be a failure (is_failure=True); "
        "SOURCE_UNAVAILABLE is NOT a failure (is_failure=False) — the "
        "distinction is what makes the mis-classification silent."
    )
    # The CRASH witnesses tuple carries the typed §1.10 fields so the
    # diagnostic surface is visible in the aggregate triage report.
    assert "ValueError" in bench_result.witnesses, (
        f"bench witnesses={bench_result.witnesses!r}; expected to carry "
        f"'ValueError' as the error_type witness."
    )
    assert raise_message in bench_result.witnesses, (
        f"bench witnesses={bench_result.witnesses!r}; expected to carry the "
        f"synthesized raise message as the error_detail AND clause_text "
        f"witness."
    )
    # The clause_text witness is now part of the CRASH lane (iter4 W1 C2
    # addition) so the diagnostic snippet surfaces in the aggregate without
    # re-running extraction (§1.10 embed-snippet contract).
    clause_text_count = sum(
        1 for w in bench_result.witnesses if w == raise_message
    )
    assert clause_text_count >= 2, (
        f"bench witnesses={bench_result.witnesses!r}; expected the raise "
        f"message to appear at least twice (once as error_detail, once as "
        f"clause_text — the §1.10 embed-snippet witness iter4 W1 C2 added "
        f"to the CRASH lane)."
    )
