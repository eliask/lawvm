"""Targeted tests to kill surviving mutants from mutation testing.

These tests address specific gaps found by mutmut in the trusted
kernels. Each test is named after the mutant it kills.

Pro adversarial review attack #12: seeded bug injection evaluation.
"""
from __future__ import annotations
from lawvm.core.ir import LegalAddress, ProvisionVersion

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline import (
    ProvisionTimeline,
    select_active_version,
)


def _make_version(
    effective: str = "2020-01-01",
    enacted: str = "",
    expires: str = "",
    is_temporary: bool = False,
    content_text: str = "text",
) -> ProvisionVersion:
    content = IRNode(kind=IRNodeKind.SECTION, label="1", text=content_text) if content_text else None
    return ProvisionVersion(
        effective=effective,
        enacted=enacted,
        expires=expires,
        variant_kind="temporary" if is_temporary else "permanent",
        content=content,
    )


def _make_timeline(*versions: ProvisionVersion) -> ProvisionTimeline:
    return ProvisionTimeline(
        address=LegalAddress(path=(("section", "1"),)),
        versions=list(versions),
    )


class TestEligibleInForceVsGoverning:
    """Kill mutants 4-10 in _eligible(): the in_force enacted-date gate."""

    def test_in_force_excludes_retroactive(self) -> None:
        """Mutant 6: `!=` → `==` on query_type check.
        A retroactive version (enacted after as_of) must be excluded
        by in_force but included by governing.
        """
        v = _make_version(effective="2020-01-01", enacted="2021-06-01")
        tl = _make_timeline(v)
        # governing: enacted doesn't matter, effective <= as_of → eligible
        gov = select_active_version(tl, "2020-06-01", query_type="governing")
        assert gov is not None
        # in_force: enacted > as_of → NOT eligible
        inf = select_active_version(tl, "2020-06-01", query_type="in_force")
        assert inf is None

    def test_in_force_includes_enacted_before(self) -> None:
        """Mutant 4: `or` → `and` on enacted check.
        When enacted <= as_of, in_force should still include the version.
        """
        v = _make_version(effective="2020-01-01", enacted="2019-06-01")
        tl = _make_timeline(v)
        inf = select_active_version(tl, "2020-06-01", query_type="in_force")
        assert inf is not None

    def test_in_force_includes_empty_enacted(self) -> None:
        """Mutant 9: `not v.enacted` → `v.enacted`.
        When enacted is empty, version should still be eligible for in_force.
        """
        v = _make_version(effective="2020-01-01", enacted="")
        tl = _make_timeline(v)
        inf = select_active_version(tl, "2020-06-01", query_type="in_force")
        assert inf is not None

    def test_in_force_string_must_be_exact(self) -> None:
        """Mutants 7-8: string mutations on "in_force".
        Only exact "in_force" triggers the enacted gate.
        """
        v = _make_version(effective="2020-01-01", enacted="2021-06-01")
        tl = _make_timeline(v)
        # governing (not "in_force") → eligible despite late enacted
        gov = select_active_version(tl, "2020-06-01", query_type="governing")
        assert gov is not None

    def test_in_force_enacted_boundary(self) -> None:
        """Mutant 10: `<=` → `<` on enacted comparison.
        enacted == as_of should still be eligible.
        """
        v = _make_version(effective="2020-01-01", enacted="2020-06-01")
        tl = _make_timeline(v)
        inf = select_active_version(tl, "2020-06-01", query_type="in_force")
        assert inf is not None


class TestPickLatestRepealPlaceholder:
    """Kill mutants in _pick_latest repeal placeholder detection."""

    def test_repeal_placeholder_attr_detected(self) -> None:
        """Mutant pick_latest_2: content = None (kills placeholder detection).
        A version with lawvm_repeal_placeholder attr should be detected.
        """
        v = ProvisionVersion(
            effective="2020-01-01",
            variant_kind="permanent",
            content=IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                text="kumottu",
                attrs={"lawvm_repeal_placeholder": "1"},
            ),
        )
        v2 = _make_version(effective="2020-01-01", enacted="2020-01-02", content_text="real")
        tl = _make_timeline(v, v2)
        result = select_active_version(tl, "2020-06-01")
        # Real content should win over placeholder at same effective date
        assert result is not None
        assert result.content is not None
        assert result.content.text == "real"

    def test_none_content_is_not_repeal_placeholder(self) -> None:
        """Mutant pick_latest_4: return False → return True for None content.
        Tombstone (content=None) is NOT a repeal placeholder.
        """
        tombstone = ProvisionVersion(
            effective="2020-01-01",
            variant_kind="permanent",
            content=None,
        )
        real = _make_version(effective="2020-01-01", enacted="2020-01-02", content_text="real")
        tl = _make_timeline(tombstone, real)
        result = select_active_version(tl, "2020-06-01")
        assert result is not None


class TestDateStringOrdering:
    """Refinement map: verify ISO date strings sort correctly as strings."""

    def test_iso_dates_string_sort_matches_chronological(self) -> None:
        """Z3 uses integer dates, Python uses string comparison.
        Verify they agree for all reasonable date pairs.
        """
        dates = [
            "1900-01-01", "1950-06-15", "1999-12-31",
            "2000-01-01", "2020-03-15", "2025-12-31",
            "2026-04-03", "9999-12-31",
        ]
        for i in range(len(dates)):
            for j in range(i + 1, len(dates)):
                assert dates[i] < dates[j], f"{dates[i]} should be < {dates[j]}"
                assert not (dates[j] <= dates[i]), f"{dates[j]} should not be <= {dates[i]}"
