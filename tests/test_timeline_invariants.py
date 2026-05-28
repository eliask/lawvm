"""Tests for timeline overlay invariant validators (Phase 7).

Uses synthetic ProvisionVersion and ProvisionTimeline objects — no corpus
data required.

Run:
    uv run pytest tests/test_timeline_invariants.py -v
"""

from __future__ import annotations

from typing import Any, Literal, cast

import pytest

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    OperationSource,
    ProvisionTimeline,
    ProvisionVersion,
    ScopePredicate,
)
from lawvm.core.provenance import ExpiryOverride
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline_invariants import (
    TimelineInvariantViolation,
    check_all_timeline_invariants,
    check_all_timeline_invariants_typed,
    check_expiry_chain_preserved,
    check_no_overlapping_permanent_versions,
    check_replay_timeline_consistency,
    check_temporary_overlay_consistency,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _addr(*path: tuple[str, str]) -> LegalAddress:
    return LegalAddress(path=tuple(path))


def _pv(
    effective: str,
    *,
    enacted: str = "",
    expires: str = "",
    variant_kind: Literal["permanent", "temporary"] = "permanent",
    text: str = "content",
    content: IRNode | None = None,
    source: OperationSource | None = None,
) -> ProvisionVersion:
    """Build a ProvisionVersion with sensible defaults."""
    if content is None and text:
        content = IRNode(kind=IRNodeKind.SECTION, label="1", text=text)
    return ProvisionVersion(
        effective=effective,
        enacted=enacted or effective,
        expires=expires,
        variant_kind=variant_kind,
        content=content,
        source=source,
    )


def _tl(address: LegalAddress, versions: list[ProvisionVersion]) -> ProvisionTimeline:
    return ProvisionTimeline(address=address, versions=versions)


def test_timeline_invariant_violation_freezes_detail_recursively() -> None:
    detail: dict[str, Any] = {"selection": {"candidates": ["a"]}}

    violation = TimelineInvariantViolation(
        kind="content_mismatch",
        section_label="1",
        address_path="section:1",
        message="content mismatch",
        detail=detail,
    )
    detail["selection"]["candidates"].append("mutated")

    assert violation.detail == {"selection": {"candidates": ("a",)}}
    frozen_detail = cast(Any, violation.detail)
    with pytest.raises(TypeError, match="immutable"):
        frozen_detail["extra"] = "blocked"


def _forged_pv(
    effective: str,
    *,
    expires: str,
    variant_kind: Literal["permanent", "temporary"] = "temporary",
) -> ProvisionVersion:
    """Construct a corrupted imported version without running post-init guards."""
    version = object.__new__(ProvisionVersion)
    version.effective = effective
    version.enacted = effective
    version.expires = expires
    version.variant_kind = variant_kind
    version.content = IRNode(kind=IRNodeKind.SECTION, label="1", text="forged")
    version.source = OperationSource(statute_id="test/forged")
    version.applicability = ()
    version.content_hash = ""
    return version


# ---------------------------------------------------------------------------
# 1. check_no_overlapping_permanent_versions
# ---------------------------------------------------------------------------


def test_no_overlap_clean() -> None:
    """Two non-overlapping permanent versions produce no violations."""
    addr = _addr(("section", "1"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv("2020-01-01", text="v1"),
                _pv("2023-06-01", text="v2"),
            ],
        ),
    }
    violations = check_no_overlapping_permanent_versions(timelines)
    assert violations == []


def test_overlap_detected() -> None:
    """Two permanent versions with same effective AND enacted date flag a violation."""
    addr = _addr(("section", "2"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv("2020-01-01", enacted="2020-01-01", text="v1"),
                _pv("2020-01-01", enacted="2020-01-01", text="v2"),
            ],
        ),
    }
    violations = check_no_overlapping_permanent_versions(timelines)
    assert len(violations) == 1
    assert "2 permanent versions" in violations[0]
    assert "2020-01-01" in violations[0]


def test_same_effective_different_enacted_is_clean() -> None:
    """Same effective but different enacted dates are resolved by tie-breaking."""
    addr = _addr(("section", "3"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv("2020-01-01", enacted="2019-12-01", text="v1"),
                _pv("2020-01-01", enacted="2019-12-15", text="v2"),
            ],
        ),
    }
    violations = check_no_overlapping_permanent_versions(timelines)
    assert violations == []


# ---------------------------------------------------------------------------
# 2. check_temporary_overlay_consistency
# ---------------------------------------------------------------------------


def test_temporary_overlay_valid() -> None:
    """One permanent + one temporary with proper expires produces no violations."""
    addr = _addr(("section", "4"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv("2020-01-01", text="permanent base"),
                _pv("2022-01-01", expires="2023-12-31", variant_kind="temporary", text="temporary overlay"),
            ],
        ),
    }
    violations = check_temporary_overlay_consistency(timelines)
    assert violations == []


def test_temporary_no_expires() -> None:
    """Temporary version without expires date is a valid unknown-expiry carrier."""
    src = OperationSource(statute_id="2022/100", enacted="2022-01-01")
    ver = _pv("2022-01-01", variant_kind="temporary", text="temp", expires="", source=src)
    assert ver.variant_kind == "temporary"
    assert ver.expires == ""


def test_temporary_expires_before_effective() -> None:
    """Temporary with expires < effective is caught at construction time."""
    import pytest

    with pytest.raises(ValueError, match="expires.*before effective"):
        _pv("2022-06-01", expires="2022-01-01", variant_kind="temporary", text="backwards")


def test_typed_temporary_bad_interval_is_not_overlap() -> None:
    """Corrupt imported temporaries should not be classified as real overlaps."""
    addr = _addr(("section", "6"))
    timelines = {addr: _tl(addr, [_forged_pv("2022-06-01", expires="2022-01-01")])}

    typed = check_all_timeline_invariants_typed(
        IRNode(kind=IRNodeKind.BODY, children=()),
        timelines,
        "2025-01-01",
    )

    assert any(v.kind == "temporary_bad_interval" for v in typed)
    assert all(v.kind != "temporary_overlap" for v in typed)


def test_temporary_overlap() -> None:
    """Two temporaries at same address with overlapping ranges produce a violation."""
    addr = _addr(("section", "7"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv("2022-01-01", expires="2023-06-01", variant_kind="temporary", text="temp1"),
                _pv("2023-01-01", expires="2024-01-01", variant_kind="temporary", text="temp2"),
            ],
        ),
    }
    violations = check_temporary_overlay_consistency(timelines)
    assert len(violations) == 1
    assert "overlapping" in violations[0]


def test_temporary_non_overlapping_is_clean() -> None:
    """Two non-overlapping temporaries produce no violations."""
    addr = _addr(("section", "8"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv("2020-01-01", expires="2020-12-31", variant_kind="temporary", text="temp1"),
                _pv("2021-06-01", expires="2022-06-01", variant_kind="temporary", text="temp2"),
            ],
        ),
    }
    violations = check_temporary_overlay_consistency(timelines)
    assert violations == []


# ---------------------------------------------------------------------------
# 3. check_expiry_chain_preserved
# ---------------------------------------------------------------------------


def test_expiry_chain_monotonic_is_clean() -> None:
    """A properly monotonic expiry chain produces no violations."""
    addr = _addr(("section", "9"))
    src = OperationSource(
        statute_id="2020/100",
        expires="2022-12-31",
        expires_original="2021-12-31",
        expiry_chain=(
            ExpiryOverride(
                source_statute_id="2021/200",
                new_expires="2022-12-31",
            ),
        ),
    )
    timelines = {
        addr: _tl(
            addr,
            [
                _pv(
                    "2020-01-01",
                    expires="2022-12-31",
                    variant_kind="temporary",
                    text="extended",
                    source=src,
                ),
            ],
        ),
    }
    violations = check_expiry_chain_preserved(timelines)
    assert violations == []


def test_expiry_chain_non_monotonic() -> None:
    """A chain where a later extension has an earlier expiry flags a violation."""
    addr = _addr(("section", "10"))
    src = OperationSource(
        statute_id="2020/100",
        expires="2022-06-30",
        expires_original="2021-12-31",
        expiry_chain=(
            ExpiryOverride(
                source_statute_id="2021/200",
                new_expires="2023-12-31",
            ),
            ExpiryOverride(
                source_statute_id="2022/300",
                new_expires="2022-06-30",  # earlier than previous!
            ),
        ),
    )
    timelines = {
        addr: _tl(
            addr,
            [
                _pv(
                    "2020-01-01",
                    expires="2022-06-30",
                    variant_kind="temporary",
                    text="regressed",
                    source=src,
                ),
            ],
        ),
    }
    violations = check_expiry_chain_preserved(timelines)
    assert len(violations) == 1
    assert "not monotonically increasing" in violations[0]


def test_expiry_chain_empty_new_expires() -> None:
    """A chain entry with empty new_expires flags a violation."""
    addr = _addr(("section", "11"))
    src = OperationSource(
        statute_id="2020/100",
        expires="2021-12-31",
        expires_original="2021-12-31",
        expiry_chain=(
            ExpiryOverride(
                source_statute_id="2021/200",
                new_expires="",
            ),
        ),
    )
    timelines = {
        addr: _tl(
            addr,
            [
                _pv(
                    "2020-01-01",
                    expires="2021-12-31",
                    variant_kind="temporary",
                    text="missing",
                    source=src,
                ),
            ],
        ),
    }
    violations = check_expiry_chain_preserved(timelines)
    assert len(violations) == 1
    assert "empty new_expires" in violations[0]


def test_expiry_chain_skipped_when_no_chain_field() -> None:
    """Standard OperationSource without expiry_chain is silently skipped."""
    addr = _addr(("section", "12"))
    src = OperationSource(
        statute_id="2020/100",
        expires="2022-12-31",
    )
    timelines = {
        addr: _tl(
            addr,
            [
                _pv("2020-01-01", expires="2022-12-31", variant_kind="temporary", text="no chain", source=src),
            ],
        ),
    }
    violations = check_expiry_chain_preserved(timelines)
    assert violations == []


# ---------------------------------------------------------------------------
# 4. check_replay_timeline_consistency
# ---------------------------------------------------------------------------


def test_replay_timeline_consistency_clean() -> None:
    """Matching IR and timelines produce no violations."""
    section = IRNode(kind=IRNodeKind.SECTION, label="1", text="Hello world")
    body = IRNode(kind=IRNodeKind.BODY, children=(section,))
    addr = _addr(("section", "1"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv(
                    "2020-01-01",
                    text="Hello world",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="Hello world"),
                ),
            ],
        ),
    }
    violations = check_replay_timeline_consistency(body, timelines, "2025-01-01")
    assert violations == []


def test_replay_timeline_ir_without_timeline() -> None:
    """IR node present but no timeline entry flags a violation."""
    section = IRNode(kind=IRNodeKind.SECTION, label="99", text="orphan")
    body = IRNode(kind=IRNodeKind.BODY, children=(section,))
    addr_99 = _addr(("section", "99"))
    # Empty timelines — no entry for section 99
    timelines: dict[LegalAddress, ProvisionTimeline] = {}
    violations = check_replay_timeline_consistency(body, timelines, "2025-01-01")
    assert any("IR_WITHOUT_TIMELINE" in v for v in violations)


def test_replay_timeline_timeline_without_ir() -> None:
    """Active timeline version but missing from IR flags a violation."""
    body = IRNode(kind=IRNodeKind.BODY, children=())
    addr = _addr(("section", "1"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv("2020-01-01", text="should be present"),
            ],
        ),
    }
    violations = check_replay_timeline_consistency(body, timelines, "2025-01-01")
    assert any("TIMELINE_WITHOUT_IR" in v for v in violations)


def test_replay_timeline_tombstone_not_flagged() -> None:
    """Tombstone (repealed) timeline entries are not flagged as missing from IR."""
    body = IRNode(kind=IRNodeKind.BODY, children=())
    addr = _addr(("section", "1"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv("2020-01-01", text="", content=None),  # tombstone
            ],
        ),
    }
    violations = check_replay_timeline_consistency(body, timelines, "2025-01-01")
    assert not any("TIMELINE_WITHOUT_IR" in v for v in violations)


def test_replay_timeline_consistency_flags_nested_section_content_mismatch() -> None:
    """Chapter-nested section mismatches should still be reported."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(IRNode(kind=IRNodeKind.SECTION, label="2", text="IR text"),),
            ),
        ),
    )
    addr = _addr(("chapter", "1"), ("section", "2"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv(
                    "2020-01-01",
                    text="timeline text",
                    content=IRNode(kind=IRNodeKind.SECTION, label="2", text="timeline text"),
                ),
            ],
        ),
    }

    violations = check_replay_timeline_consistency(body, timelines, "2025-01-01")

    assert any("CONTENT_MISMATCH" in v for v in violations)


def test_replay_timeline_consistency_understands_supplement_roots() -> None:
    """Top-level supplements must be visible to replay/timeline consistency checks."""
    statute = IRStatute(
        statute_id="test/supplement",
        title="Supplement invariant test",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
        supplements=(IRNode(kind=IRNodeKind.SCHEDULE, label="1", text="Schedule text"),),
    )
    addr = _addr(("schedule", "1"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv(
                    "2020-01-01",
                    text="Schedule text",
                    content=IRNode(kind=IRNodeKind.SCHEDULE, label="1", text="Schedule text"),
                ),
            ],
        ),
    }

    violations = check_replay_timeline_consistency(statute, timelines, "2025-01-01")
    assert violations == []


def test_replay_timeline_consistency_preserves_ambiguous_scope_note() -> None:
    """Omitted required scope should be surfaced explicitly, not collapsed."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="England text"),),
    )
    addr = _addr(("section", "1"))
    timelines = {
        addr: _tl(
            addr,
            [
                ProvisionVersion(
                    effective="2000-01-01",
                    enacted="2000-01-01",
                    variant_kind="permanent",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="England text"),
                    applicability=[
                        ScopePredicate(
                            dimension="territory",
                            includes=frozenset({"England"}),
                        )
                    ],
                ),
            ],
        ),
    }

    violations = check_replay_timeline_consistency(body, timelines, "2025-01-01")
    assert any("ambiguous_missing_scope" in v for v in violations)

    typed = check_all_timeline_invariants_typed(body, timelines, "2025-01-01")
    violation = next(v for v in typed if v.kind == "ir_without_timeline")
    assert violation.detail["selection_status"] == "ambiguous_missing_scope"


# ---------------------------------------------------------------------------
# 5. check_all_timeline_invariants (aggregate)
# ---------------------------------------------------------------------------


def test_all_invariants_clean() -> None:
    """A well-formed timeline + matching IR produces no violations."""
    section = IRNode(kind=IRNodeKind.SECTION, label="1", text="content")
    body = IRNode(kind=IRNodeKind.BODY, children=(section,))
    addr = _addr(("section", "1"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv("2020-01-01", text="content", content=IRNode(kind=IRNodeKind.SECTION, label="1", text="content")),
            ],
        ),
    }
    violations = check_all_timeline_invariants(body, timelines, "2025-01-01")
    assert violations == []


def test_all_invariants_catches_multiple_issues() -> None:
    """Aggregate check finds violations from multiple sub-checks."""
    body = IRNode(kind=IRNodeKind.BODY, children=())

    addr1 = _addr(("section", "1"))
    addr2 = _addr(("section", "2"))

    timelines = {
        # addr1: two overlapping temporaries (both valid construction)
        addr1: _tl(
            addr1,
            [
                _pv("2020-01-01", variant_kind="temporary", text="temp1", expires="2022-12-31"),
                _pv("2021-01-01", variant_kind="temporary", text="temp2", expires="2023-12-31"),
            ],
        ),
        # addr2: active version but missing from IR
        addr2: _tl(
            addr2,
            [
                _pv("2020-01-01", text="missing from ir"),
            ],
        ),
    }

    violations = check_all_timeline_invariants(body, timelines, "2025-01-01")
    assert len(violations) >= 2
    assert any("overlap" in v.lower() or "temporary" in v.lower() for v in violations)
    assert any("TIMELINE_WITHOUT_IR" in v for v in violations)


def test_typed_invariants_classify_without_string_prefix_guessing() -> None:
    """Typed invariant output should preserve the real invariant kind."""
    addr = _addr(("section", "13"))
    timelines = {
        addr: _tl(
            addr,
            [
                _pv("2020-01-01", variant_kind="temporary", expires="2022-12-31", text="temp1"),
                _pv("2021-01-01", variant_kind="temporary", expires="2023-12-31", text="temp2"),
            ],
        ),
    }

    typed = check_all_timeline_invariants_typed(
        IRNode(kind=IRNodeKind.BODY, children=()),
        timelines,
        "2025-01-01",
    )

    assert any(v.kind == "temporary_overlap" for v in typed)
    assert all(v.kind != "content_mismatch" for v in typed)
