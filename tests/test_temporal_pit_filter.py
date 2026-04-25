"""PIT materialization filter tests for temporary amendments.

Verifies that the temporal activation pipeline correctly excludes
expired temporary nodes when materializing at a point in time, and
includes them when materializing within their validity window.

The test constructs a synthetic statute, compiles timelines directly
(bypassing the grafter), and asserts that materialize_pit returns
the expected structure at various dates. Executable expiry comes from
explicit TemporalEvent carriers; source provenance is evidence only.

This is the acceptance test for the temporal activation wire-up
described in the task: explicit TemporalEvent.expires flows through
compile_timelines → ProvisionVersion.expires → _eligible() → absent from PIT
output when as_of > expires.
"""

from __future__ import annotations

from typing import Any, cast

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    ProvisionTimeline,
    ProvisionVersion,
    ScopePredicate,
)
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.compile_result import ActivationRule, TemporalEvent, TemporalScope
from lawvm.core.timeline import compile_timelines, materialize_pit, select_active_version_ex
from lawvm.core.timeline import select_background_version


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _section(label: str, text: str = "") -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _subsection(label: str, text: str = "") -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, text=text)


def _statute(label: str, body: IRNode) -> IRStatute:
    return IRStatute(statute_id=label, title=label, body=body)


def _temporal_event_batch(
    *,
    group_id: str,
    statute_id: str,
    effective: str,
    expires: str = "",
) -> tuple[TemporalEvent, ...]:
    events = [
        TemporalEvent(
            event_id=f"{group_id}:commence",
            group_id=group_id,
            kind="commence",
            scope=TemporalScope(target_statute=statute_id),
            effective=effective,
            activation_rule=ActivationRule(kind="fixed_date", effective_date=effective),
            source=OperationSource(statute_id=f"{statute_id}:source"),
        )
    ]
    if expires:
        events.append(
            TemporalEvent(
                event_id=f"{group_id}:expire",
                group_id=group_id,
                kind="expire",
                scope=TemporalScope(target_statute=statute_id),
                expires=expires,
                source=OperationSource(statute_id=f"{statute_id}:source"),
            )
        )
    return tuple(events)


def _section_text(statute: IRStatute, chapter: str, section: str) -> str:
    """Read a nested chapter/section text helper for this test file."""
    chapter_node = next(
        c for c in statute.body.children if c.kind == IRNodeKind.CHAPTER and c.label == chapter
    )
    section_node = next(
        c for c in chapter_node.children if c.kind == IRNodeKind.SECTION and c.label == section
    )
    return section_node.text


def test_select_active_version_rejects_empty_as_of() -> None:
    import pytest

    timeline = ProvisionTimeline(
        address=LegalAddress(path=(("section", "1"),)),
        versions=[
            ProvisionVersion(
                effective="2000-01-01",
                enacted="2000-01-01",
                content=IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),
            ),
        ],
    )

    with pytest.raises(Exception, match="as_of must be non-empty"):
        select_active_version_ex(timeline, "")


def test_standalone_expire_event_expires_existing_section() -> None:
    base = _statute("9999/standalone-expire", _body(_section("1", "Base section")))

    timelines = compile_timelines(
        base,
        [],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:standalone-expire",
                kind="expire",
                scope=TemporalScope(
                    target_statute="9999/standalone-expire",
                    exact_addresses=(LegalAddress(path=(("section", "1"),)),),
                ),
                expires="2010-01-01",
            ),
        ),
    )

    assert select_active_version_ex(timelines[LegalAddress(path=(("section", "1"),))], "2009-12-31").version is not None
    assert select_active_version_ex(timelines[LegalAddress(path=(("section", "1"),))], "2010-01-01").version is None


def test_standalone_applicability_event_restricts_existing_section() -> None:
    addr = LegalAddress(path=(("section", "1"),))
    base = _statute("9999/standalone-applicability", _body(_section("1", "Base section")))

    timelines = compile_timelines(
        base,
        [],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:standalone-applicability",
                kind="set_applicability",
                effective="2010-01-01",
                scope=TemporalScope(
                    target_statute="9999/standalone-applicability",
                    exact_addresses=(addr,),
                    predicates=(
                        ScopePredicate(
                            dimension="territory",
                            includes=frozenset({"AX"}),
                        ),
                    ),
                ),
            ),
        ),
    )

    assert select_active_version_ex(timelines[addr], "2009-01-01").status == "selected"
    assert select_active_version_ex(timelines[addr], "2011-01-01").status == "ambiguous_missing_scope"
    assert select_active_version_ex(timelines[addr], "2011-01-01", territory="AX").status == "selected"


def test_standalone_revive_event_restores_expired_section() -> None:
    addr = LegalAddress(path=(("section", "1"),))
    base = _statute("9999/standalone-revive", _body(_section("1", "Base section")))

    timelines = compile_timelines(
        base,
        [],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:standalone-expire",
                kind="expire",
                scope=TemporalScope(
                    target_statute="9999/standalone-revive",
                    exact_addresses=(addr,),
                ),
                expires="2005-01-01",
            ),
            TemporalEvent(
                event_id="ev:standalone-revive",
                kind="revive",
                scope=TemporalScope(
                    target_statute="9999/standalone-revive",
                    exact_addresses=(addr,),
                ),
                effective="2010-01-01",
            ),
        ),
    )

    assert select_active_version_ex(timelines[addr], "2006-01-01").version is None
    revived = select_active_version_ex(timelines[addr], "2011-01-01").version
    assert revived is not None
    assert revived.content is not None
    assert revived.content.text == "Base section"


def test_standalone_commence_event_restores_prior_substantive_content() -> None:
    addr = LegalAddress(path=(("section", "1"),))
    base = _statute("9999/standalone-commence", _body(_section("1", "Base section")))

    timelines = compile_timelines(
        base,
        [],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:standalone-expire",
                kind="expire",
                scope=TemporalScope(
                    target_statute="9999/standalone-commence",
                    exact_addresses=(addr,),
                ),
                expires="2005-01-01",
            ),
            TemporalEvent(
                event_id="ev:standalone-commence",
                kind="commence",
                scope=TemporalScope(
                    target_statute="9999/standalone-commence",
                    exact_addresses=(addr,),
                ),
                effective="2010-01-01",
            ),
        ),
    )

    assert select_active_version_ex(timelines[addr], "2006-01-01").version is None
    commenced = select_active_version_ex(timelines[addr], "2011-01-01").version
    assert commenced is not None
    assert commenced.content is not None
    assert commenced.content.text == "Base section"


def test_compile_timelines_exact_target_applies_update() -> None:
    """Exact targets apply their update in PIT materialization."""
    base = _statute(
        "9999/exact-default",
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(IRNode(kind=IRNodeKind.SECTION, label="3", text="Base section"),),
            ),
        ),
    )
    op = LegalOperation(
        op_id="replace_sparse",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "1"), ("section", "3"))),
        payload=IRNode(kind=IRNodeKind.SECTION, label="3", text="Updated section"),
        group_id="g:sparse-default",
        source=OperationSource(
            statute_id="2020/1",
            enacted="2010-01-01",
            effective="2010-01-01",
        ),
    )
    events = _temporal_event_batch(
        group_id="g:sparse-default",
        statute_id=base.statute_id,
        effective="2010-01-01",
    )
    timelines = compile_timelines(base, [op], base_date="2000-01-01", temporal_events=events)
    pit = materialize_pit(timelines, "2010-12-31", base=base)
    assert _section_text(pit, "1", "3") == "Updated section"


def test_compile_timelines_sparse_target_stays_base_without_exact_match() -> None:
    """Sparse targets do not apply without an exact base address."""
    base = _statute(
        "9999/sparse-off",
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(IRNode(kind=IRNodeKind.SECTION, label="3", text="Base section"),),
            ),
        ),
    )
    op = LegalOperation(
        op_id="replace_sparse",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "3"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="3", text="Updated section"),
        group_id="g:sparse-off",
        source=OperationSource(
            statute_id="2020/1",
            enacted="2010-01-01",
            effective="2010-01-01",
        ),
    )
    events = _temporal_event_batch(
        group_id="g:sparse-off",
        statute_id=base.statute_id,
        effective="2010-01-01",
    )
    timelines = compile_timelines(base, [op], base_date="2000-01-01", temporal_events=events)
    pit = materialize_pit(timelines, "2010-12-31", base=base)
    assert _section_text(pit, "1", "3") == "Base section"


# ---------------------------------------------------------------------------
# Core scenario: one temporary INSERT, present / absent at PIT
# ---------------------------------------------------------------------------


class TestTemporaryInsertPITFilter:
    """A temporary INSERT disappears from PIT output after its expiry date."""

    def _build(self) -> tuple[IRStatute, list[LegalOperation], tuple[TemporalEvent, ...]]:
        """Build base statute and the temporary insert op."""
        base = _statute(
            "9999/1",
            _body(
                _section("1", "Permanent text of section 1"),
                _section("2", "Permanent text of section 2"),
            ),
        )
        # Temporary INSERT of section 3, valid 2020-05-01 to 2022-12-31
        op = LegalOperation(
            op_id="insert_s3_temp",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "3"),)),
            payload=_section("3", "Temporary text of section 3"),
            group_id="g:insert-s3-temp",
            source=OperationSource(
                statute_id="2020/101",
                enacted="2020-05-01",
                effective="2020-05-01",
                expires="2022-12-31",
            ),
        )
        events = _temporal_event_batch(
            group_id="g:insert-s3-temp",
            statute_id=base.statute_id,
            effective="2020-05-01",
            expires="2022-12-31",
        )
        return base, [op], events

    def test_absent_before_effective(self) -> None:
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        pit = materialize_pit(timelines, "2019-12-31", base=base)
        labels = [c.label for c in pit.body.children]
        assert "3" not in labels

    def test_present_on_effective_date(self) -> None:
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        pit = materialize_pit(timelines, "2020-05-01", base=base)
        labels = [c.label for c in pit.body.children]
        assert "3" in labels
        s3_text = next(c.text for c in pit.body.children if c.label == "3")
        assert "Temporary text of section 3" in s3_text

    def test_present_mid_window(self) -> None:
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        pit = materialize_pit(timelines, "2021-01-01", base=base)
        labels = [c.label for c in pit.body.children]
        assert "3" in labels

    def test_present_day_before_expiry(self) -> None:
        """The section is present on the day immediately before expiry.

        Note: the timeline uses exclusive expiry semantics (expires > as_of in
        _eligible()), so a version with expires="2022-12-31" is absent on
        "2022-12-31" and present on "2022-12-30".
        """
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        pit = materialize_pit(timelines, "2022-12-30", base=base)
        labels = [c.label for c in pit.body.children]
        assert "3" in labels

    def test_absent_after_expiry(self) -> None:
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        pit = materialize_pit(timelines, "2023-01-01", base=base)
        labels = [c.label for c in pit.body.children]
        assert "3" not in labels

    def test_permanent_sections_unaffected(self) -> None:
        """Sections 1 and 2 are present at all dates."""
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        for date in ("2019-01-01", "2021-06-01", "2024-01-01"):
            pit = materialize_pit(timelines, date, base=base)
            labels = [c.label for c in pit.body.children]
            assert "1" in labels, f"section 1 missing at {date}"
            assert "2" in labels, f"section 2 missing at {date}"


# ---------------------------------------------------------------------------
# Subsection-level temporary INSERT
# ---------------------------------------------------------------------------


class TestTemporarySubsectionPITFilter:
    """Temporary INSERT at subsection level respects expiry at PIT materialization."""

    def _build(self) -> tuple[IRStatute, list[LegalOperation], tuple[TemporalEvent, ...]]:
        base = _statute(
            "9999/2",
            _body(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5",
                    text="",
                    children=(
                        _subsection("1", "Paragraph 1 of section 5"),
                        _subsection("2", "Paragraph 2 of section 5"),
                    ),
                ),
            ),
        )
        # Temporary insert of subsection 3 in section 5
        op = LegalOperation(
            op_id="insert_s5_mom3_temp",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "5"), ("subsection", "3"))),
            payload=_subsection("3", "Temporary paragraph 3"),
            group_id="g:insert-s5-sub3-temp",
            source=OperationSource(
                statute_id="2020/202",
                enacted="2020-01-01",
                effective="2020-01-01",
                expires="2022-12-31",
            ),
        )
        events = _temporal_event_batch(
            group_id="g:insert-s5-sub3-temp",
            statute_id=base.statute_id,
            effective="2020-01-01",
            expires="2022-12-31",
        )
        return base, [op], events

    def test_present_during_window(self) -> None:
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        pit = materialize_pit(timelines, "2021-01-01", base=base)
        s5 = next((c for c in pit.body.children if c.label == "5"), None)
        assert s5 is not None
        sub_labels = [c.label for c in s5.children]
        assert "3" in sub_labels

    def test_absent_after_expiry(self) -> None:
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        pit = materialize_pit(timelines, "2023-01-01", base=base)
        s5 = next((c for c in pit.body.children if c.label == "5"), None)
        assert s5 is not None
        sub_labels = [c.label for c in s5.children]
        assert "3" not in sub_labels


# ---------------------------------------------------------------------------
# Dual-date horizon: expires_as_of splitting from effective as_of
# ---------------------------------------------------------------------------


class TestSplitExpiresAsOf:
    """materialize_pit expires_as_of splits effective and expiry horizons.

    The primary use case is finlex_oracle mode:
    - effective horizon = "9999-12-31" (include ALL amendments regardless of
      effective date)
    - expiry horizon = oracle PIT date (temporary sections active at snapshot
      date are correctly included/excluded)
    """

    def _build(self) -> tuple[IRStatute, list[LegalOperation], tuple[TemporalEvent, ...]]:
        """Base statute + temporary section with expires="2022-12-31"."""
        base = _statute(
            "9999/3",
            _body(
                _section("1", "Permanent section"),
            ),
        )
        op = LegalOperation(
            op_id="insert_s2_temp",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "2"),)),
            payload=_section("2", "Temporary section"),
            group_id="g:insert-s2-temp",
            source=OperationSource(
                statute_id="2020/500",
                enacted="2020-01-01",
                effective="2020-01-01",
                expires="2022-12-31",
            ),
        )
        events = _temporal_event_batch(
            group_id="g:insert-s2-temp",
            statute_id=base.statute_id,
            effective="2020-01-01",
            expires="2022-12-31",
        )
        return base, [op], events

    def test_expires_as_of_within_window_includes_section(self) -> None:
        """With expires_as_of inside the validity window, section IS present."""
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        # effective as_of="9999-12-31" would normally include everything;
        # expires_as_of="2021-11-19" is before expiry, so section is still active.
        pit = materialize_pit(timelines, "9999-12-31", base=base, expires_as_of="2021-11-19")
        labels = [c.label for c in pit.body.children]
        assert "2" in labels, "temporary section should be present when expires_as_of < expires"

    def test_expires_as_of_after_expiry_excludes_section(self) -> None:
        """With expires_as_of past the expiry date, section is NOT present."""
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        # expires_as_of="2023-01-01" is after expires="2022-12-31", so expired.
        pit = materialize_pit(timelines, "9999-12-31", base=base, expires_as_of="2023-01-01")
        labels = [c.label for c in pit.body.children]
        assert "2" not in labels, "temporary section should be absent when expires_as_of > expires"

    def test_expires_as_of_empty_falls_back_to_as_of(self) -> None:
        """When expires_as_of is empty, expiry check uses as_of."""
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        # as_of="2021-01-01" is within window and expires_as_of="" → fallback to as_of.
        pit_present = materialize_pit(timelines, "2021-01-01", base=base, expires_as_of="")
        assert "2" in [c.label for c in pit_present.body.children]
        # as_of="2023-01-01" is after expiry and expires_as_of="" → fallback to as_of.
        pit_absent = materialize_pit(timelines, "2023-01-01", base=base, expires_as_of="")
        assert "2" not in [c.label for c in pit_absent.body.children]

    def test_permanent_section_unaffected_by_expires_as_of(self) -> None:
        """Permanent sections are not affected by any expires_as_of value."""
        base, ops, temporal_events = self._build()
        timelines = compile_timelines(base, ops, base_date="2010-01-01", temporal_events=temporal_events)
        for expires_as_of in ("2019-01-01", "2021-06-01", "2025-01-01"):
            pit = materialize_pit(timelines, "9999-12-31", base=base, expires_as_of=expires_as_of)
            assert "1" in [c.label for c in pit.body.children], (
                f"permanent section 1 missing with expires_as_of={expires_as_of}"
            )


def test_decoupled_expiry_horizon_keeps_permanent_background_over_future_repeal_placeholder() -> None:
    """An inactive future repeal placeholder should not erase a permanent background.

    Finland's finlex_oracle replay uses an open-ended effective horizon with a
    separate expiry horizon.  That means a future repeal placeholder may be
    present in the timeline without yet being active at the oracle snapshot.
    The selector must still surface the permanent background in that case.
    """
    addr = LegalAddress(path=(("section", "29h"),))
    permanent = ProvisionVersion(
        effective="2024-12-30",
        enacted="2024-12-19",
        expires="",
        variant_kind="permanent",
        content=_section("29h", "Permanent text"),
    )
    repeal_placeholder = ProvisionVersion(
        effective="2026-01-01",
        enacted="2025-12-22",
        expires="2026-01-01",
        variant_kind="temporary",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="29h",
            attrs={"lawvm_repeal_placeholder": "1"},
        ),
    )
    timeline = ProvisionTimeline(address=addr, versions=[permanent, repeal_placeholder])

    bg = select_background_version(timeline, "9999-12-31", expires_as_of="2026-01-01")
    active = select_active_version_ex(timeline, "9999-12-31", expires_as_of="2026-01-01")

    assert bg is not None
    assert bg.content is not None
    assert bg.content.label == "29h"
    assert active.status == "selected"
    assert active.version is not None
    assert active.version.content is not None
    assert active.version.content.label == "29h"


def test_decoupled_expiry_horizon_ignores_future_permanent_tombstone() -> None:
    """Detached-horizon materialization must ignore future permanent tombstones."""
    addr = LegalAddress(path=(("section", "29h"),))
    permanent = ProvisionVersion(
        effective="2024-12-30",
        enacted="2024-12-19",
        expires="",
        variant_kind="permanent",
        content=_section("29h", "Permanent text"),
    )
    repeal_tombstone = ProvisionVersion(
        effective="2026-01-01",
        enacted="2025-12-22",
        expires="",
        variant_kind="permanent",
        content=None,
    )
    timeline = ProvisionTimeline(address=addr, versions=[permanent, repeal_tombstone])

    bg = select_background_version(timeline, "9999-12-31", expires_as_of="2025-12-22")
    active = select_active_version_ex(timeline, "9999-12-31", expires_as_of="2025-12-22")

    assert bg is not None
    assert bg.content is not None
    assert bg.content.label == "29h"
    assert active.status == "selected"
    assert active.version is not None
    assert active.version.content is not None
    assert active.version.content.label == "29h"


def test_decoupled_expiry_horizon_applies_same_day_permanent_tombstone() -> None:
    """Detached-horizon materialization must honor same-day permanent repeals."""
    addr = LegalAddress(path=(("section", "29h"),))
    permanent = ProvisionVersion(
        effective="2024-12-30",
        enacted="2024-12-19",
        expires="",
        variant_kind="permanent",
        content=_section("29h", "Permanent text"),
    )
    repeal_tombstone = ProvisionVersion(
        effective="2026-01-01",
        enacted="2025-12-22",
        expires="",
        variant_kind="permanent",
        content=None,
    )
    timeline = ProvisionTimeline(address=addr, versions=[permanent, repeal_tombstone])

    bg = select_background_version(timeline, "9999-12-31", expires_as_of="2026-01-01")
    active = select_active_version_ex(timeline, "9999-12-31", expires_as_of="2026-01-01")

    assert bg is not None
    assert bg.content is None
    assert active.status == "selected"
    assert active.version is not None
    assert active.version.content is None


def test_plain_horizon_keeps_permanent_background_over_future_repeal_placeholder() -> None:
    """Ordinary PIT queries must ignore future temporary placeholders."""
    addr = LegalAddress(path=(("section", "29h"),))
    permanent = ProvisionVersion(
        effective="2024-12-30",
        enacted="2024-12-19",
        expires="",
        variant_kind="permanent",
        content=_section("29h", "Permanent text"),
    )
    repeal_placeholder = ProvisionVersion(
        effective="2026-01-01",
        enacted="2025-12-22",
        expires="2026-01-01",
        variant_kind="temporary",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="29h",
            attrs={"lawvm_repeal_placeholder": "1"},
        ),
    )
    timeline = ProvisionTimeline(address=addr, versions=[permanent, repeal_placeholder])

    bg = select_background_version(timeline, "2025-01-01")
    active = select_active_version_ex(timeline, "2025-01-01")

    assert bg is not None
    assert bg.content is not None
    assert bg.content.label == "29h"
    assert active.status == "selected"
    assert active.version is not None
    assert active.version.content is not None
    assert active.version.content.label == "29h"


# ---------------------------------------------------------------------------
# temporary versions without parseable expiry
# ---------------------------------------------------------------------------


class TestTemporaryWithoutExpiry:
    """ProvisionVersion with temporary expiry omitted."""

    def test_provision_version_temporary_without_expires(self) -> None:
        """temporary without expires represents unknown expiry."""
        from lawvm.core.ir import ProvisionVersion

        ver = ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            expires="",
            variant_kind="temporary",
            content=None,
        )
        assert ver.variant_kind == "temporary"
        assert ver.expires == ""

    def test_provision_version_temporary_unresolved_is_rejected(self) -> None:
        """The temporary_unresolved carrier is rejected."""
        import pytest
        from lawvm.core.ir import ProvisionVersion

        with pytest.raises(ValueError, match="variant_kind must be one of"):
            ProvisionVersion(
                effective="2020-01-01",
                enacted="2020-01-01",
                expires="",
                variant_kind=cast(Any, "temporary_unresolved"),
                content=None,
            )

    def test_eligible_temporary_without_expires_as_of_included(self) -> None:
        """temporary without expires is INCLUDED when expires_as_of is empty."""
        from lawvm.core.ir import ProvisionVersion
        from lawvm.core.timeline import _eligible

        ver = ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            expires="",
            variant_kind="temporary",
            content=None,
        )
        assert _eligible(ver, "2025-01-01", "governing", expires_as_of="")

    def test_eligible_temporary_without_expires_as_of_included_with_expiry_horizon(self) -> None:
        """temporary without expires is still INCLUDED when expires_as_of is set."""
        from lawvm.core.ir import ProvisionVersion
        from lawvm.core.timeline import _eligible

        ver = ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            expires="",
            variant_kind="temporary",
            content=None,
        )
        assert _eligible(ver, "2025-01-01", "governing", expires_as_of="2025-01-01")

    def test_compile_timelines_ignores_missing_expiry_for_variant_kind(self) -> None:
        """compile_timelines does not let provenance-only missing expiry decide variant_kind."""
        base = _statute(
            "1999/488",
            _body(
                _section("1", "Base section"),
            ),
        )
        op = LegalOperation(
            op_id="temp_no_expiry",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "2"),)),
            payload=_section("2", "Temporary section without expiry"),
            group_id="g:temp-no-expiry",
            source=OperationSource(
                statute_id="2005/100",
                enacted="2005-01-01",
                effective="2005-01-01",
                expires="",
            ),
        )
        temporal_events = _temporal_event_batch(
            group_id="g:temp-no-expiry",
            statute_id=base.statute_id,
            effective="2005-01-01",
        )
        timelines = compile_timelines(base, [op], base_date="1999-01-01", temporal_events=temporal_events)
        addr = LegalAddress(path=(("section", "2"),))
        assert addr in timelines
        versions = timelines[addr].versions
        op_versions = [v for v in versions if v.content is not None and v.content.label == "2"]
        assert len(op_versions) == 1
        assert op_versions[0].variant_kind == "permanent"

    def test_missing_expiry_provenance_is_included_in_pit_without_expires_as_of(self) -> None:
        """missing-expiry provenance does not hide the version when no expiry horizon is set."""
        base = _statute(
            "1999/488",
            _body(_section("1", "Base")),
        )
        op = LegalOperation(
            op_id="temp_no_expiry",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "2"),)),
            payload=_section("2", "Temp without expiry"),
            group_id="g:temp-no-expiry",
            source=OperationSource(
                statute_id="2005/100",
                enacted="2005-01-01",
                effective="2005-01-01",
                expires="",
            ),
        )
        temporal_events = _temporal_event_batch(
            group_id="g:temp-no-expiry",
            statute_id=base.statute_id,
            effective="2005-01-01",
        )
        timelines = compile_timelines(base, [op], base_date="1999-01-01", temporal_events=temporal_events)
        pit = materialize_pit(timelines, "2025-01-01", base=base, expires_as_of="")
        labels = [c.label for c in pit.body.children]
        assert "2" in labels, "missing-expiry provenance should not hide the version"

    def test_missing_expiry_provenance_is_not_special_cased_with_expires_as_of(self) -> None:
        """missing-expiry provenance does not trigger conservative exclusion when expires_as_of is set."""
        base = _statute(
            "1999/488",
            _body(_section("1", "Base")),
        )
        op = LegalOperation(
            op_id="temp_no_expiry",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "2"),)),
            payload=_section("2", "Temp without expiry"),
            group_id="g:temp-no-expiry",
            source=OperationSource(
                statute_id="2005/100",
                enacted="2005-01-01",
                effective="2005-01-01",
                expires="",
            ),
        )
        temporal_events = _temporal_event_batch(
            group_id="g:temp-no-expiry",
            statute_id=base.statute_id,
            effective="2005-01-01",
        )
        timelines = compile_timelines(base, [op], base_date="1999-01-01", temporal_events=temporal_events)
        # finlex_oracle mode: expires_as_of set to a concrete PIT date
        pit = materialize_pit(timelines, "9999-12-31", base=base, expires_as_of="2025-01-01")
        labels = [c.label for c in pit.body.children]
        assert "2" in labels, "missing-expiry provenance should not be treated as executable temporal authority"
