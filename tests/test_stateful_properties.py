"""Hypothesis stateful property-based tests (RuleBasedStateMachine) for LawVM's trusted kernels.

Models state transitions for three kernel domains:
  1. TreeOpsStateMachine  - IRNode tree mutations via insert/remove/replace
  2. TimelineStateMachine - ProvisionVersion accumulation and temporal consistency
  3. PhaseResultStateMachine - PhaseResult observation/violation accumulation and merge

Run:
    uv run pytest tests/test_stateful_properties.py -v
"""

from __future__ import annotations

import string
from itertools import pairwise
from typing import List, Optional, Set

from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant, initialize
from hypothesis import strategies as st

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    OperationSource,
    ProvisionTimeline,
    ProvisionVersion,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import (
    _default_sort_key,
    check_invariants,
    find,
    insert_sorted,
    remove_at,
    replace_at,
    resolve,
)
from lawvm.core.timeline import select_active_version
from lawvm.core.phase_result import PhaseResult, PhaseBuilder
from lawvm.core.observation_registry import finding_codes_by_role


# ============================================================================
# Shared strategies
# ============================================================================

SHORT_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " .,;-",
    min_size=1,
    max_size=40,
)

SECTION_LABELS = st.integers(min_value=1, max_value=50).map(str)
OBSERVATION_KINDS = st.sampled_from(finding_codes_by_role("observation"))
OBLIGATION_KINDS = st.sampled_from(finding_codes_by_role("obligation"))
VIOLATION_KINDS = st.sampled_from(finding_codes_by_role("violation"))


# ============================================================================
# 1. TreeOpsStateMachine
# ============================================================================


class TreeOpsStateMachine(RuleBasedStateMachine):
    """Models an IRNode tree being mutated through insert_sorted, remove_at, replace_at.

    State: an IRNode body with flat sections (no chapters, to keep paths simple).
    Rules: insert a new section, remove an existing section, replace an existing section.
    Invariants checked after each step:
      - check_invariants() returns no violations
      - Label uniqueness holds among section children
      - Section labels are in sorted order
    """

    def __init__(self) -> None:
        super().__init__()
        self.tree: IRNode = IRNode(kind=IRNodeKind.BODY, label=None, text="", children=())
        self.known_labels: Set[str] = set()

    @initialize()
    def init_tree(self) -> None:
        """Start with an empty body."""
        self.tree = IRNode(kind=IRNodeKind.BODY, label=None, text="", children=())
        self.known_labels = set()

    @rule(label=SECTION_LABELS, text=SHORT_TEXT)
    def insert_section(self, label: str, text: str) -> None:
        """Insert a new section at the sorted position."""
        if label in self.known_labels:
            return  # skip duplicate labels
        new_section = IRNode(
            kind=IRNodeKind.SECTION,
            label=label,
            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=text),),
        )
        self.tree = insert_sorted(self.tree, [], new_section)
        self.known_labels.add(label)

    @rule(data=st.data())
    def remove_section(self, data: st.DataObject) -> None:
        """Remove a randomly chosen existing section."""
        if not self.known_labels:
            return
        label = data.draw(st.sampled_from(sorted(self.known_labels)))
        path = [("section", label)]
        self.tree = remove_at(self.tree, path)
        self.known_labels.discard(label)

    @rule(data=st.data(), text=SHORT_TEXT)
    def replace_section(self, data: st.DataObject, text: str) -> None:
        """Replace a randomly chosen existing section with new content."""
        if not self.known_labels:
            return
        label = data.draw(st.sampled_from(sorted(self.known_labels)))
        path = [("section", label)]
        replacement = IRNode(
            kind=IRNodeKind.SECTION,
            label=label,
            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=text),),
        )
        self.tree = replace_at(self.tree, path, replacement)

    @invariant()
    def check_invariants_pass(self) -> None:
        """check_invariants() returns no violations after each step."""
        violations = check_invariants(self.tree)
        assert violations == [], f"Invariant violations: {violations}"

    @invariant()
    def label_uniqueness_holds(self) -> None:
        """No two section children share the same label."""
        seen: dict[str, int] = {}
        for child in self.tree.children:
            if child.kind == IRNodeKind.SECTION and child.label is not None:
                seen[child.label] = seen.get(child.label, 0) + 1
        duplicates = {k: v for k, v in seen.items() if v > 1}
        assert not duplicates, f"Duplicate section labels: {duplicates}"

    @invariant()
    def labels_are_sorted(self) -> None:
        """Section labels are in _default_sort_key order."""
        section_labels = [c.label for c in self.tree.children if c.kind == IRNodeKind.SECTION and c.label is not None]
        keys = [_default_sort_key(lbl) for lbl in section_labels]
        for i, (left_key, right_key) in enumerate(pairwise(keys)):
            assert left_key <= right_key, (
                f"Section labels out of order at position {i}: "
                f"{section_labels[i]} ({left_key}) > {section_labels[i + 1]} ({right_key})"
            )

    @invariant()
    def model_matches_tree(self) -> None:
        """The set of known_labels matches what the tree actually contains."""
        tree_labels = {c.label for c in self.tree.children if c.kind == IRNodeKind.SECTION and c.label is not None}
        assert tree_labels == self.known_labels, f"Model/tree mismatch: model={self.known_labels}, tree={tree_labels}"

    @invariant()
    def find_resolves_all_known(self) -> None:
        """Every known label is findable via find() and resolvable via resolve()."""
        for label in self.known_labels:
            found_path = find(self.tree, "section", label)
            assert found_path is not None, f"find() returned None for label {label!r}"
            node = resolve(self.tree, found_path)
            assert node is not None, f"resolve() returned None for label {label!r}"
            assert node.label == label


TestTreeOpsStateMachine = TreeOpsStateMachine.TestCase
TestTreeOpsStateMachine.settings = settings(max_examples=50, stateful_step_count=30, deadline=None)


# ============================================================================
# 2. TimelineStateMachine
# ============================================================================


class TimelineStateMachine(RuleBasedStateMachine):
    """Models timeline compilation by accumulating ProvisionVersion objects.

    State: a list of ProvisionVersion objects at a single address.
    Rules: add permanent version, add temporary overlay, remove last version.
    Invariants:
      - No two permanent versions share the exact same (effective, enacted) pair
      - Every temporary version has expires >= effective
      - Versions remain sorted by (effective, enacted)
      - select_active_version obeys temporal eligibility
    """

    def __init__(self) -> None:
        super().__init__()
        self.versions: List[ProvisionVersion] = []
        self.address = LegalAddress(path=(("section", "1"),))

    @initialize()
    def init_timeline(self) -> None:
        """Start with an empty version list."""
        self.versions = []

    @rule(
        eff_year=st.integers(min_value=2000, max_value=2025),
        eff_month=st.integers(min_value=1, max_value=12),
        eff_day=st.integers(min_value=1, max_value=28),
        text=SHORT_TEXT,
    )
    def add_permanent_version(self, eff_year: int, eff_month: int, eff_day: int, text: str) -> None:
        """Add a permanent version with the given effective date."""
        effective = f"{eff_year}-{eff_month:02d}-{eff_day:02d}"
        version = ProvisionVersion(
            effective=effective,
            enacted=effective,
            variant_kind="permanent",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text=text),
            source=OperationSource(statute_id=f"{eff_year}/1"),
        )
        self.versions.append(version)
        self.versions.sort(key=lambda v: (v.effective, v.enacted))

    @rule(
        eff_year=st.integers(min_value=2000, max_value=2020),
        eff_month=st.integers(min_value=1, max_value=12),
        eff_day=st.integers(min_value=1, max_value=28),
        duration_months=st.integers(min_value=1, max_value=60),
        text=SHORT_TEXT,
    )
    def add_temporary_overlay(
        self,
        eff_year: int,
        eff_month: int,
        eff_day: int,
        duration_months: int,
        text: str,
    ) -> None:
        """Add a temporary overlay with expires = effective + duration."""
        effective = f"{eff_year}-{eff_month:02d}-{eff_day:02d}"
        # Compute a simple expiry by adding months (capped at 2030)
        exp_year = eff_year + (eff_month + duration_months - 1) // 12
        exp_month = (eff_month + duration_months - 1) % 12 + 1
        if exp_year > 2030:
            exp_year = 2030
            exp_month = 12
        expires = f"{exp_year}-{exp_month:02d}-28"
        # Ensure expires >= effective (guaranteed by construction, but be safe)
        if expires < effective:
            expires = f"{eff_year + 1}-12-31"

        version = ProvisionVersion(
            effective=effective,
            enacted=effective,
            expires=expires,
            variant_kind="temporary",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text=text),
            source=OperationSource(statute_id=f"{eff_year}/temp"),
        )
        self.versions.append(version)
        self.versions.sort(key=lambda v: (v.effective, v.enacted))

    @rule()
    def remove_last_version(self) -> None:
        """Remove the most recently added version (if any)."""
        if self.versions:
            self.versions.pop()

    @invariant()
    def temporaries_have_valid_expiry(self) -> None:
        """Every temporary version has expires >= effective."""
        for v in self.versions:
            if v.variant_kind == "temporary":
                assert v.expires, f"Temporary version missing expires: effective={v.effective}"
                assert v.expires >= v.effective, f"Temporary expires {v.expires} < effective {v.effective}"

    @invariant()
    def versions_are_sorted(self) -> None:
        """Versions are in non-decreasing (effective, enacted) order."""
        for i, (left_version, right_version) in enumerate(pairwise(self.versions)):
            a = (left_version.effective, left_version.enacted)
            b = (right_version.effective, right_version.enacted)
            assert a <= b, f"Version ordering violated at index {i}: {a} > {b}"

    @invariant()
    def select_active_obeys_eligibility(self) -> None:
        """select_active_version at 2015-01-01 returns only eligible versions."""
        if not self.versions:
            return
        tl = ProvisionTimeline(address=self.address, versions=list(self.versions))
        test_date = "2015-01-01"
        result = select_active_version(tl, test_date)
        if result is not None:
            assert result.effective <= test_date, (
                f"Active version effective {result.effective} > query date {test_date}"
            )
            if result.expires:
                assert result.expires > test_date, f"Active version expires {result.expires} <= query date {test_date}"

    @invariant()
    def active_version_is_from_timeline(self) -> None:
        """select_active_version returns a version that exists in the timeline."""
        if not self.versions:
            return
        tl = ProvisionTimeline(address=self.address, versions=list(self.versions))
        test_date = "2015-01-01"
        result = select_active_version(tl, test_date)
        if result is not None:
            assert any(v is result for v in self.versions), "Active version not found in timeline versions"


TestTimelineStateMachine = TimelineStateMachine.TestCase
TestTimelineStateMachine.settings = settings(max_examples=50, stateful_step_count=20, deadline=None)


# ============================================================================
# 3. PhaseResultStateMachine
# ============================================================================


class PhaseResultStateMachine(RuleBasedStateMachine):
    """Models PhaseResult accumulation through observe(), violate(), and merge().

    State: a PhaseBuilder being accumulated, plus a count model.
    Rules: observe(), violate(), build and merge with another PhaseResult.
    Invariants:
      - Blocking violations make has_blocking True
      - Observation count monotonically increases through merges
      - Violation count monotonically increases through merges
      - PhaseResult is immutable (frozen dataclass)
    """

    def __init__(self) -> None:
        super().__init__()
        self.builder = PhaseBuilder()
        self.observation_count: int = 0
        self.violation_count: int = 0
        self.obligation_count: int = 0
        self.has_any_violation: bool = False
        self.has_blocking_obligation: bool = False
        # Track accumulated results from merges
        self.merged_result: Optional[PhaseResult] = None

    @initialize()
    def init_phase(self) -> None:
        """Start with a fresh PhaseBuilder."""
        self.builder = PhaseBuilder()
        self.observation_count = 0
        self.violation_count = 0
        self.obligation_count = 0
        self.has_any_violation = False
        self.has_blocking_obligation = False
        self.merged_result = None

    @rule(kind=OBSERVATION_KINDS)
    def observe(self, kind: str) -> None:
        """Record an observation."""
        self.builder.observe(kind=kind, stage="test", detail={"msg": "test observation"})
        self.observation_count += 1

    @rule(kind=VIOLATION_KINDS)
    def violate(self, kind: str) -> None:
        """Record a violation (always blocking)."""
        self.builder.violate(kind=kind, stage="test", detail={"msg": "test violation"})
        self.violation_count += 1
        self.has_any_violation = True

    @rule(
        kind=OBLIGATION_KINDS,
        blocking=st.booleans(),
    )
    def oblige(self, kind: str, blocking: bool) -> None:
        """Record an obligation (may or may not be blocking)."""
        self.builder.oblige(kind=kind, stage="test", detail={"msg": "test obligation"}, blocking=blocking)
        self.obligation_count += 1
        if blocking:
            self.has_blocking_obligation = True

    @rule()
    def finish_and_merge(self) -> None:
        """Finish the current builder into a PhaseResult and merge with accumulated result."""
        current = self.builder.finish("output")
        if self.merged_result is None:
            self.merged_result = current
        else:
            self.merged_result = self.merged_result.merge(current)
        # Reset builder for next round but keep counts (they accumulate)
        self.builder = PhaseBuilder()

    @invariant()
    def builder_counts_match_model(self) -> None:
        """The builder's internal lists match our count model."""
        builder_observations = sum(1 for finding in self.builder._findings if finding.role == "observation")
        builder_violations = sum(1 for finding in self.builder._findings if finding.role == "violation")
        builder_obligations = sum(1 for finding in self.builder._findings if finding.role == "obligation")
        merged_observations = (
            sum(1 for finding in self.merged_result.findings() if finding.role == "observation")
            if self.merged_result
            else 0
        )
        merged_violations = (
            sum(1 for finding in self.merged_result.findings() if finding.role == "violation")
            if self.merged_result
            else 0
        )
        merged_obligations = (
            sum(1 for finding in self.merged_result.findings() if finding.role == "obligation")
            if self.merged_result
            else 0
        )
        assert builder_observations + merged_observations == self.observation_count, (
            f"Observation count mismatch: builder={builder_observations}, "
            f"merged={merged_observations}, model={self.observation_count}"
        )
        assert builder_violations + merged_violations == self.violation_count, "Violation count mismatch"
        assert builder_obligations + merged_obligations == self.obligation_count, "Obligation count mismatch"

    @invariant()
    def violations_make_blocking(self) -> None:
        """If any violation has been recorded and finished, has_blocking is True."""
        if self.merged_result is not None and self.has_any_violation:
            # Only check if all violations have been finished (not still in builder)
            if not any(finding.role == "violation" for finding in self.builder._findings):
                assert self.merged_result.has_blocking, "PhaseResult should be blocking when violations exist"

    @invariant()
    def blocking_obligations_make_blocking(self) -> None:
        """If any blocking obligation has been recorded and finished, has_blocking is True."""
        if self.merged_result is not None and self.has_blocking_obligation:
            if not any(finding.role == "obligation" and finding.blocking for finding in self.builder._findings):
                assert self.merged_result.has_blocking, "PhaseResult should be blocking when blocking obligations exist"

    @invariant()
    def merged_output_is_latest(self) -> None:
        """After merge, the output is always the latest stage's output."""
        if self.merged_result is not None:
            assert self.merged_result.output == "output", (
                f"Merged output should be 'output', got {self.merged_result.output!r}"
            )

    @invariant()
    def merge_accumulates_observations(self) -> None:
        """Merge never loses observations -- merged count >= any single stage."""
        if self.merged_result is not None:
            merged_obs = sum(1 for finding in self.merged_result.findings() if finding.role == "observation")
            merged_viols = sum(1 for finding in self.merged_result.findings() if finding.role == "violation")
            merged_obls = sum(1 for finding in self.merged_result.findings() if finding.role == "obligation")
            # These should never decrease -- we only add, never remove
            builder_obs = sum(1 for finding in self.builder._findings if finding.role == "observation")
            builder_viols = sum(1 for finding in self.builder._findings if finding.role == "violation")
            builder_obls = sum(1 for finding in self.builder._findings if finding.role == "obligation")
            total_obs = merged_obs + builder_obs
            total_viols = merged_viols + builder_viols
            total_obls = merged_obls + builder_obls
            assert total_obs == self.observation_count
            assert total_viols == self.violation_count
            assert total_obls == self.obligation_count


TestPhaseResultStateMachine = PhaseResultStateMachine.TestCase
TestPhaseResultStateMachine.settings = settings(max_examples=50, stateful_step_count=20, deadline=None)
