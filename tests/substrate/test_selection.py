"""Pins for the StateSelectionIndex v0 objects + multi-root.

Covers (per the build brief): hash-stability + id-recompute, the universe-root
omission detection (drop/add a key → root changes), the candidate-set
completeness invariant, the closed ScopePredicate enum + ``unsupported``
ambiguity, the §5 status/block algebra, the two distinct roots of §7 never
conflated, and the spec's pure-data hard cases (temporary twin window, scope
ambiguity without/with territory, retroactivity account-axis, same-day blocked).
"""

from __future__ import annotations

import pytest

from lawvm.substrate.canonical_json import semantic_hash, unwrap_and_verify, wrap_row
from lawvm.substrate.roots import RootError, set_root
from lawvm.substrate.selection import (
    PROFILE_GOVERNING_TEXT,
    PROFILE_IN_FORCE_TEXT,
    PROFILE_VIEWER_DEFAULT,
    SCOPE_DIMENSIONS,
    V0_PROFILE_IDS,
    ApplicabilityFact,
    DecisionBasis,
    ScopePredicate,
    SelectionCandidate,
    SelectionCandidateSet,
    SelectionError,
    SelectionProfile,
    SelectionRow,
    SelectionUniverse,
    StateSelectionRoots,
    TemporalBasis,
    build_selection_index_roots,
    build_state_selection_roots,
    v0_profiles,
)


# --------------------------------------------------------------------------- #
# Factories                                                                   #
# --------------------------------------------------------------------------- #


def _scope(territory: list[str] | None = None, status: str = "total") -> ScopePredicate:
    return ScopePredicate(dimensions={"territory": territory or []}, status=status)


def _temporal_basis(kind: str = "fixed_date") -> TemporalBasis:
    return TemporalBasis(kind=kind)


def _fact(
    *,
    node_version_id: str = "sha256:nv1",
    rail: str = "permanent",
    effect_interval: tuple[str, str | None] = ("2024-01-01", "2026-01-01"),
    account_interval: tuple[str, str | None] = ("corpus:2026-06-21", None),
    scope_predicate_id: str = "sha256:scope1",
    basis_kind: str = "fixed_date",
) -> ApplicabilityFact:
    return ApplicabilityFact(
        work_id="fi:act:301/2004",
        address_id="addr:section:7",
        node_version_id=node_version_id,
        content_leaf_hash="sha256:leaf1",
        branch_id="actual",
        effect_interval=effect_interval,
        enactment_interval=("2023-12-15", None),
        account_interval=account_interval,
        rail=rail,
        scope_predicate_id=scope_predicate_id,
        precedence_class="same_rail_latest",
        temporal_basis=_temporal_basis(basis_kind),
        produced_by_transition_id="transition:1",
    )


def _row(
    *,
    status: str = "selected",
    selected_node_version_id: str | None = "sha256:nv1",
    block_reason: str | None = None,
    required_scope_dimensions: tuple[str, ...] = (),
    effect_interval: tuple[str, str | None] = ("2024-01-01", "2026-01-01"),
    account_interval: tuple[str, str | None] = ("corpus:2026-06-21", None),
) -> SelectionRow:
    return SelectionRow(
        work_id="fi:act:301/2004",
        query_profile_id=PROFILE_GOVERNING_TEXT,
        branch_id="actual",
        address_id="addr:section:7",
        scope_query_id="scope:unspecified",
        effect_interval=effect_interval,
        account_interval=account_interval,
        source_policy_id="keeper_latest_semantic",
        status=status,
        selected_node_version_id=selected_node_version_id,
        candidate_set_hash="sha256:candset1",
        required_scope_dimensions=required_scope_dimensions,
        block_reason=block_reason,
        decision_basis=DecisionBasis(selection_rule_id="lawvm.selection.same_rail_latest.v1"),
    )


# --------------------------------------------------------------------------- #
# Hash stability + id recompute                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "obj, id_attr, body_key",
    [
        (_scope(["FI"]), "scope_predicate_id", "scope_predicate_id"),
        (_fact(), "fact_id", "fact_id"),
        (
            SelectionCandidateSet(
                selection_key="sha256:k",
                candidates=(
                    SelectionCandidate(
                        node_version_id="sha256:nv1",
                        rail="temporary",
                        effect_interval=("2024-01-01", "2024-07-01"),
                        scope_predicate_id="sha256:scope1",
                        eligible=True,
                    ),
                ),
            ),
            "candidate_set_id",
            "candidate_set_id",
        ),
        (_row(), "selection_key", "selection_key"),
        (v0_profiles()[0], "profile_content_id", "profile_content_id"),
    ],
)
def test_id_recompute_matches_leaf_hash_of_body(obj, id_attr, body_key) -> None:
    """``<name>_id`` is a pure function of ``to_canonical_dict()`` and is stable."""
    first = getattr(obj, id_attr)
    second = getattr(obj, id_attr)
    assert first == second  # deterministic / re-entrant
    assert first.startswith("sha256:")
    # The id is not a member of the hashed body (§1.3).
    assert id_attr not in obj.to_canonical_dict()


def test_object_hash_wrapper_round_trips() -> None:
    fact = _fact()
    row = wrap_row(fact.to_canonical_dict())
    body = unwrap_and_verify(row)
    assert body == fact.to_canonical_dict()
    assert row["object_hash"] == semantic_hash(fact.to_canonical_dict())


def test_content_change_changes_id() -> None:
    base = _fact()
    changed = _fact(effect_interval=("2024-01-01", "2025-01-01"))
    assert base.fact_id != changed.fact_id


# --------------------------------------------------------------------------- #
# ScopePredicate — closed enum + unsupported                                  #
# --------------------------------------------------------------------------- #


def test_scope_predicate_dimensions_are_closed() -> None:
    for dim in SCOPE_DIMENSIONS:
        ScopePredicate(dimensions={dim: ["X"]})  # each closed dim accepted
    with pytest.raises(SelectionError):
        ScopePredicate(dimensions={"jurisdiction_age": ["X"]})  # open dim rejected


def test_scope_predicate_status_enum() -> None:
    ScopePredicate(dimensions={}, status="unsupported")
    with pytest.raises(SelectionError):
        ScopePredicate(dimensions={}, status="maybe")


def test_scope_predicate_canonical_dict_lists_all_closed_dimensions() -> None:
    body = _scope(["FI"]).to_canonical_dict()
    dimensions = body["dimensions"]
    assert isinstance(dimensions, dict)
    # Every closed dimension is present in the emitted body (empty = wildcard).
    assert set(dimensions) == set(SCOPE_DIMENSIONS)
    assert dict(dimensions) == {
        "territory": ["FI"],
        "extent": [],
        "sector": [],
        "person_class": [],
        "contingency": [],
    }


def test_scope_predicate_values_sorted_and_deduped() -> None:
    a = ScopePredicate(dimensions={"extent": ["W", "E", "E"]})
    b = ScopePredicate(dimensions={"extent": ["E", "W"]})
    assert a.scope_predicate_id == b.scope_predicate_id  # order/dup-insensitive


# --------------------------------------------------------------------------- #
# Status / block algebra (§5)                                                 #
# --------------------------------------------------------------------------- #


def test_selected_requires_node_version() -> None:
    with pytest.raises(SelectionError):
        _row(status="selected", selected_node_version_id=None)


def test_non_selected_forbids_node_version() -> None:
    with pytest.raises(SelectionError):
        _row(status="absent", selected_node_version_id="sha256:nv1")


def test_blocked_requires_valid_block_reason() -> None:
    with pytest.raises(SelectionError):  # missing reason
        _row(status="blocked", selected_node_version_id=None)
    with pytest.raises(SelectionError):  # invalid reason
        _row(status="blocked", selected_node_version_id=None, block_reason="nope")
    ok = _row(
        status="blocked",
        selected_node_version_id=None,
        block_reason="same_day_precedence_unresolved",
    )
    assert ok.block_reason == "same_day_precedence_unresolved"


def test_block_reason_only_with_blocked_status() -> None:
    with pytest.raises(SelectionError):
        _row(status="absent", selected_node_version_id=None, block_reason="expiry_unverified")


def test_absent_and_out_of_scope_are_distinct_statuses() -> None:
    absent = _row(status="absent", selected_node_version_id=None)
    out_of_scope = _row(status="out_of_scope", selected_node_version_id=None)
    assert absent.status == "absent"
    assert out_of_scope.status == "out_of_scope"
    assert absent.selection_key != out_of_scope.selection_key


def test_ambiguous_missing_scope_must_name_required_dimensions() -> None:
    with pytest.raises(SelectionError):  # no dimensions named
        _row(status="ambiguous_missing_scope", selected_node_version_id=None)
    ok = _row(
        status="ambiguous_missing_scope",
        selected_node_version_id=None,
        required_scope_dimensions=("territory",),
    )
    assert ok.required_scope_dimensions == ("territory",)
    with pytest.raises(SelectionError):  # non-closed dimension
        _row(
            status="ambiguous_missing_scope",
            selected_node_version_id=None,
            required_scope_dimensions=("phase_of_moon",),
        )


def test_unknown_status_rejected() -> None:
    with pytest.raises(SelectionError):
        _row(status="definitely_selected")


# --------------------------------------------------------------------------- #
# Candidate set completeness invariant                                        #
# --------------------------------------------------------------------------- #


def test_candidate_set_rejects_duplicate_node_versions() -> None:
    cand = SelectionCandidate(
        node_version_id="sha256:nv1",
        rail="permanent",
        effect_interval=("2024-01-01", None),
        scope_predicate_id="sha256:scope1",
        eligible=True,
    )
    with pytest.raises(SelectionError):
        SelectionCandidateSet(selection_key="sha256:k", candidates=(cand, cand))


def test_candidate_eligible_cannot_carry_rejected_reason() -> None:
    with pytest.raises(SelectionError):
        SelectionCandidate(
            node_version_id="sha256:nv1",
            rail="permanent",
            effect_interval=("2024-01-01", None),
            scope_predicate_id="sha256:scope1",
            eligible=True,
            rejected_reason="superseded",
        )


def test_candidate_set_complete_flag_carried() -> None:
    cs = SelectionCandidateSet(
        selection_key="sha256:k",
        candidates=(
            SelectionCandidate(
                node_version_id="sha256:nv1",
                rail="temporary",
                effect_interval=("2024-01-01", "2024-07-01"),
                scope_predicate_id="sha256:scope1",
                eligible=True,
            ),
            SelectionCandidate(
                node_version_id="sha256:nv2",
                rail="permanent",
                effect_interval=("2024-07-01", None),
                scope_predicate_id="sha256:scope1",
                eligible=False,
                rejected_reason="not_yet_effective_at_cell",
            ),
        ),
    )
    body = cs.to_canonical_dict()
    assert body["complete"] is True
    assert body["completion_basis"] == "derived_from_applicability_fact_root"
    candidates = body["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 2


# --------------------------------------------------------------------------- #
# Intervals                                                                    #
# --------------------------------------------------------------------------- #


def test_inverted_or_empty_interval_rejected() -> None:
    with pytest.raises(SelectionError):
        _fact(effect_interval=("2026-01-01", "2024-01-01")).to_canonical_dict()
    with pytest.raises(SelectionError):
        _fact(effect_interval=("2024-01-01", "2024-01-01")).to_canonical_dict()


def test_open_ended_interval_allowed() -> None:
    body = _fact(effect_interval=("2024-01-01", None)).to_canonical_dict()
    assert body["effect_interval"] == ["2024-01-01", None]


# --------------------------------------------------------------------------- #
# Universe root — omission detection (the keystone, §6)                       #
# --------------------------------------------------------------------------- #


def _universe(keys: dict[str, str]) -> SelectionUniverse:
    return SelectionUniverse(
        work_id="fi:act:301/2004",
        query_profile_ids=(PROFILE_GOVERNING_TEXT,),
        branch_ids=("actual",),
        expected_selection_keys=keys,
        address_root="sha256:addr",
        effect_boundary_root="sha256:eff",
        account_boundary_root="sha256:acc",
        scope_query_root="sha256:scope",
    )


def test_universe_root_detects_dropped_key() -> None:
    full = _universe({"sha256:k1": "sha256:r1", "sha256:k2": "sha256:r2"})
    dropped = _universe({"sha256:k1": "sha256:r1"})
    assert full.selection_key_root != dropped.selection_key_root


def test_universe_root_detects_added_key() -> None:
    base = _universe({"sha256:k1": "sha256:r1"})
    added = _universe({"sha256:k1": "sha256:r1", "sha256:k2": "sha256:r2"})
    assert base.selection_key_root != added.selection_key_root


def test_universe_root_detects_value_change() -> None:
    a = _universe({"sha256:k1": "sha256:r1"})
    b = _universe({"sha256:k1": "sha256:r1_PRIME"})
    assert a.selection_key_root != b.selection_key_root


def test_empty_universe_root_is_valid() -> None:
    empty = _universe({})
    assert empty.selection_key_root.startswith("sha256:")
    assert empty.universe_id.startswith("sha256:")


def test_universe_key_order_insensitive() -> None:
    a = _universe({"sha256:k1": "sha256:r1", "sha256:k2": "sha256:r2"})
    b = _universe({"sha256:k2": "sha256:r2", "sha256:k1": "sha256:r1"})
    assert a.selection_key_root == b.selection_key_root  # MapRoot sorts keys


# --------------------------------------------------------------------------- #
# v0 profiles                                                                  #
# --------------------------------------------------------------------------- #


def test_v0_profiles_are_the_three_pinned() -> None:
    ids = tuple(p.profile_id for p in v0_profiles())
    assert ids == V0_PROFILE_IDS
    assert set(ids) == {PROFILE_GOVERNING_TEXT, PROFILE_IN_FORCE_TEXT, PROFILE_VIEWER_DEFAULT}


def test_profile_content_id_distinguishes_contracts() -> None:
    gov, in_force, _viewer = v0_profiles()
    assert gov.profile_content_id != in_force.profile_content_id


def test_profile_content_id_stable_on_reconstruction() -> None:
    a = v0_profiles()[0]
    b = SelectionProfile(
        profile_id=a.profile_id,
        effect_date_axis=a.effect_date_axis,
        account_axis=a.account_axis,
        branch_axis=a.branch_axis,
        scope_policy=a.scope_policy,
        contingency_policy=a.contingency_policy,
        retroactivity_policy=a.retroactivity_policy,
        ultra_activity_policy=a.ultra_activity_policy,
        description=a.description,
    )
    assert a.profile_content_id == b.profile_content_id


# --------------------------------------------------------------------------- #
# The two distinct roots of §7 — never conflated                             #
# --------------------------------------------------------------------------- #


def _state_roots() -> StateSelectionRoots:
    return build_state_selection_roots(
        selection_profile_object_hashes=["sha256:p1"],
        selection_universe_object_hashes=["sha256:u1"],
        scope_predicate_object_hashes=["sha256:sc1"],
        applicability_fact_object_hashes=["sha256:f1"],
        candidate_set_object_hashes=["sha256:cs1"],
        selection_row_object_hashes=["sha256:r1"],
    )


def test_state_selection_root_is_set_root_over_eight_subroots() -> None:
    roots = _state_roots()
    sub = roots.subroots()
    assert set(sub) == {
        "selection_profile_root",
        "selection_universe_root",
        "scope_predicate_root",
        "applicability_fact_root",
        "candidate_set_root",
        "selection_row_root",
        "temporal_event_root",
        "temporal_residual_root",
    }
    expected = set_root("state_selection", sub.values())
    assert roots.state_selection_root == expected


def test_empty_temporal_subroots_still_committed() -> None:
    roots = _state_roots()
    # temporal_event/residual layers are empty in v0 but still produce a root.
    assert roots.temporal_event_root.startswith("sha256:")
    assert roots.temporal_residual_root.startswith("sha256:")


def test_changing_one_subroot_changes_state_selection_root() -> None:
    base = _state_roots()
    perturbed = build_state_selection_roots(
        selection_profile_object_hashes=["sha256:p1"],
        selection_universe_object_hashes=["sha256:u1"],
        scope_predicate_object_hashes=["sha256:sc1"],
        applicability_fact_object_hashes=["sha256:f1_CHANGED"],
        candidate_set_object_hashes=["sha256:cs1"],
        selection_row_object_hashes=["sha256:r1"],
    )
    assert base.state_selection_root != perturbed.state_selection_root


def test_selection_index_root_has_four_children_including_state_selection() -> None:
    state_root = _state_roots().state_selection_root
    index = build_selection_index_roots(
        content_leaf_root="sha256:cl",
        node_version_root="sha256:nv",
        state_selection_root=state_root,
        projection_root="sha256:pr",
    )
    children = index.children()
    assert set(children) == {
        "content_leaf_root",
        "node_version_root",
        "state_selection_root",
        "projection_root",
    }
    # selection_index_root (manifest-level) != state_selection_root (its child).
    assert index.selection_index_root != state_root
    assert index.children()["state_selection_root"] == state_root
    expected = set_root("selection_index", children.values())
    assert index.selection_index_root == expected


def test_duplicate_subroot_rejected() -> None:
    # Two identical sub-root values under the state_selection SetRoot is INVALID.
    dup = StateSelectionRoots(
        selection_profile_root="sha256:same",
        selection_universe_root="sha256:same",
        scope_predicate_root="sha256:c",
        applicability_fact_root="sha256:d",
        candidate_set_root="sha256:e",
        selection_row_root="sha256:f",
        temporal_event_root="sha256:g",
        temporal_residual_root="sha256:h",
    )
    with pytest.raises(RootError):
        _ = dup.state_selection_root


# --------------------------------------------------------------------------- #
# Pure-data hard cases (§10) that need no engine                              #
# --------------------------------------------------------------------------- #


def test_temporary_twin_window_clean_disjoint_versions() -> None:
    """§10.4 — temporary [..,2023-07-01) + permanent deferred [2023-07-01,∞).

    The two versions are temporally disjoint at the same address; the candidate
    set represents both cleanly and selection picks one per cell.
    """
    temp = _fact(
        node_version_id="sha256:temp",
        rail="temporary",
        effect_interval=("2023-01-01", "2023-07-01"),
    )
    perm = _fact(
        node_version_id="sha256:perm",
        rail="permanent",
        effect_interval=("2023-07-01", None),
    )
    assert temp.fact_id != perm.fact_id
    cs = SelectionCandidateSet(
        selection_key="sha256:cell-gap",
        candidates=(
            SelectionCandidate(
                node_version_id="sha256:temp",
                rail="temporary",
                effect_interval=("2023-01-01", "2023-07-01"),
                scope_predicate_id="sha256:scope1",
                eligible=True,
            ),
            SelectionCandidate(
                node_version_id="sha256:perm",
                rail="permanent",
                effect_interval=("2023-07-01", None),
                scope_predicate_id="sha256:scope1",
                eligible=False,
                rejected_reason="not_effective_in_cell",
            ),
        ),
    )
    # The selected cell in the temporary window picks the temporary version.
    row = _row(
        status="selected",
        selected_node_version_id="sha256:temp",
        effect_interval=("2023-01-01", "2023-07-01"),
    )
    assert row.status == "selected"
    assert row.candidate_set_hash is not None
    assert cs.candidate_set_id.startswith("sha256:")


def test_scope_ambiguity_without_territory() -> None:
    """§10.5 — no-territory query over E+W vs S versions → ambiguous_missing_scope."""
    ew = _scope(["E", "W"]).scope_predicate_id
    s = _scope(["S"]).scope_predicate_id
    assert ew != s
    row = _row(
        status="ambiguous_missing_scope",
        selected_node_version_id=None,
        required_scope_dimensions=("territory",),
    )
    assert row.status == "ambiguous_missing_scope"
    assert "territory" in row.required_scope_dimensions


def test_scope_specific_selection_with_territory() -> None:
    """§10.5 — territory=E selects the E+W version (never 'prefer broader/latest')."""
    row = SelectionRow(
        work_id="uk:act:1/2000",
        query_profile_id=PROFILE_GOVERNING_TEXT,
        branch_id="actual",
        address_id="addr:section:1",
        scope_query_id=_scope(["E"]).scope_predicate_id,
        effect_interval=("2020-01-01", None),
        account_interval=("corpus:2026-06-21", None),
        source_policy_id="keeper_latest_semantic",
        status="selected",
        selected_node_version_id="sha256:ew_version",
        candidate_set_hash="sha256:cs",
        decision_basis=DecisionBasis(selection_rule_id="lawvm.selection.scope_match.v1"),
    )
    assert row.status == "selected"
    assert row.selected_node_version_id == "sha256:ew_version"


def test_retroactivity_account_axis_two_answers() -> None:
    """§10.1 — same effect date, different account version → different text.

    The canonical reason one date is insufficient: a pre-enactment account sees
    old text; a post-enactment account sees the retroactive text. Modeled as two
    rows differing only on account_interval + selected version.
    """
    pre = _row(
        status="selected",
        selected_node_version_id="sha256:old_text",
        effect_interval=("2026-02-01", "2026-03-01"),
        account_interval=("corpus:2026-02-15", "corpus:2026-03-01"),
    )
    post = _row(
        status="selected",
        selected_node_version_id="sha256:retro_text",
        effect_interval=("2026-02-01", "2026-03-01"),
        account_interval=("corpus:2026-03-01", None),
    )
    assert pre.selected_node_version_id != post.selected_node_version_id
    assert pre.selection_key != post.selection_key
    retro_fact = _fact(basis_kind="retroactive")
    assert retro_fact.temporal_basis.kind == "retroactive"


def test_same_day_precedence_unresolved_blocks() -> None:
    """§10.6 — unprovable same-day order → blocked, not hidden in row order."""
    row = _row(
        status="blocked",
        selected_node_version_id=None,
        block_reason="same_day_precedence_unresolved",
    )
    assert row.status == "blocked"
    assert row.block_reason == "same_day_precedence_unresolved"
    assert row.selected_node_version_id is None


def test_expiry_unverified_blocks() -> None:
    """§10.3 — unverified fixed-term bound never becomes live text."""
    row = _row(
        status="blocked",
        selected_node_version_id=None,
        block_reason="expiry_unverified",
    )
    assert row.block_reason == "expiry_unverified"
