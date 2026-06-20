from __future__ import annotations

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.provenance import MigrationEvent
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland import replay_fold_timeline_backfill as backfill


def test_fold_backfill_reuses_preview_for_same_active_migration_set(monkeypatch) -> None:
    raw_timelines = {}
    cache: dict[object, object] = {}
    calls = 0

    def fake_preview(**kwargs):
        nonlocal calls
        calls += 1
        return backfill.FoldTimelineBackfillResult(
            records=(),
            raw_timelines=raw_timelines,
            rekeyed_timelines={},
        )

    monkeypatch.setattr(backfill, "_preview_rekeyed_timelines", fake_preview)

    replay_fold_ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="fold text"),),
            ),
        ),
    )
    migration_events = (
        MigrationEvent(
            event_id="mig:fixture:section:9->section:10",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "9"),)),
            to_address=LegalAddress(path=(("section", "10"),)),
            effective="2000-01-01",
            source_statute="2000/1",
        ),
    )

    for as_of in ("2001-01-01", "2002-01-01"):
        result = backfill.append_fold_timeline_backfill_ops(
            lo_ops=[],
            replay_fold_ir=replay_fold_ir,
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_statute_id="1999/1",
            migration_events=migration_events,
            as_of=as_of,
            preview_raw_timelines=raw_timelines,
            preview_rekeyed_timelines_cache=cache,
        )
        assert len(result.backfill_ops) == 1

    assert calls == 1
