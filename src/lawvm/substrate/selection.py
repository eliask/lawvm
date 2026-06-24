"""``StateSelectionIndex`` v0 — the sparse selection-index objects + multi-root.

Spec: ``notes_internal/STATE_SELECTION_INDEX_V0.md`` (object schemas §3, profiles
§4, status/block algebra §5, universe §6, root construction §7) with shared
conventions from ``notes_internal/OBJECT_MODEL_AND_PACK_V0.md`` (canonical-JSON
§1, root functions §2, the additive ``selection_index_root`` split §2.4) and the
six frozen decisions of ``DISTRIBUTABLE_LAW_SUBSTRATE_DESIGN.md §21``.

This module is the load-bearing replacement for dense ``active_at``: an
engine-authored, certificate-rooted, **sparse** map from a declared selection
query — ``(effect_date, account_version, branch, scope, query_profile)`` — to a
selected ``node_version`` *or* a typed non-selection reason. The browser does
**interval lookup only** (§8) and never performs temporal legal reasoning; the
hard temporal semantics live in the :class:`ApplicabilityFact` objects the
engine produces.

Every object is a frozen ``@dataclass(frozen=True, slots=True)`` carrying a
``to_canonical_dict()`` (the ``lawvm.canonical_json.v1`` body, NFC-normalized at
construction, **without** its own id) and a computed ``@property <name>_id``
derived as ``leaf_hash(domain, body_without_id)`` — mirroring
:class:`lawvm.substrate.manifest.PackManifest`. The id is never a member of the
body it hashes (§1.3).

The module is jurisdiction-NEUTRAL. ``corpus_version`` / account tokens are
plain strings of the form ``"<j>:corpus:<date>"`` (e.g. ``"fi:corpus:2026-06-21"``)
or the bare ``"corpus:<date>"`` form used in the spec examples — never an
imported P1 ``SourceBundleVersion`` object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from lawvm.substrate.canonical_json import JsonValue, nfc
from lawvm.substrate.roots import leaf_hash, map_root, set_root

# --------------------------------------------------------------------------- #
# Schemas + domains                                                           #
# --------------------------------------------------------------------------- #

_SCHEMA_APPLICABILITY_FACT = "lawvm.applicability_fact.v1"
_SCHEMA_SELECTION_ROW = "lawvm.selection_row.v1"
_SCHEMA_CANDIDATE_SET = "lawvm.selection_candidate_set.v1"
_SCHEMA_SCOPE_PREDICATE = "lawvm.scope_predicate.v1"
_SCHEMA_SELECTION_PROFILE = "lawvm.selection_profile.v1"
_SCHEMA_SELECTION_UNIVERSE = "lawvm.selection_universe.v1"

# Leaf-hash domains (one per object kind; never reused across kinds).
_DOMAIN_APPLICABILITY_FACT = "applicability_fact"
_DOMAIN_SELECTION_ROW = "selection_row"
_DOMAIN_CANDIDATE_SET = "selection_candidate_set"
_DOMAIN_SCOPE_PREDICATE = "scope_predicate"
_DOMAIN_SELECTION_PROFILE = "selection_profile"
_DOMAIN_SELECTION_UNIVERSE = "selection_universe"


class SelectionError(ValueError):
    """A StateSelectionIndex object violates a v0 schema invariant."""


# --------------------------------------------------------------------------- #
# Closed enums (frozen vocabularies)                                          #
# --------------------------------------------------------------------------- #

# §3.4 / design §21 #5 — ScopePredicate is CLOSED at the substrate boundary.
# A browser must never silently evaluate an unsupported dimension; the richer
# frontend form projects to closed-or-`unsupported`.
SCOPE_DIMENSIONS: tuple[str, ...] = (
    "territory",
    "extent",
    "sector",
    "person_class",
    "contingency",
)
SCOPE_PREDICATE_STATUSES: frozenset[str] = frozenset(
    {"total", "ambiguous", "unsupported"}
)

# §5 — top-level selection statuses (kept small; block_reason carries detail).
SELECTION_STATUSES: frozenset[str] = frozenset(
    {
        "selected",
        "absent",
        "ambiguous_missing_scope",
        "blocked",
        "out_of_scope",
        "unsupported_profile",
    }
)

# §5 — block_reason algebra (only meaningful when status == "blocked").
BLOCK_REASONS: frozenset[str] = frozenset(
    {
        "timeline_unverified",
        "expiry_unverified",
        "contingent_commencement_unresolved",
        "event_bound_unresolved",
        "source_policy_unclassified",
        "same_day_precedence_unresolved",
        "scope_predicate_unsupported",
        "source_artifacts_missing",
        "temporal_doctrine_unmodeled",
    }
)

# §3.1 — applicability-fact rails (the richer substrate field over variant_kind).
RAILS: frozenset[str] = frozenset(
    {"permanent", "temporary", "proposal", "tombstone", "expired"}
)

# §3.1 — temporal-basis kinds (why one date is insufficient).
TEMPORAL_BASIS_KINDS: frozenset[str] = frozenset(
    {
        "fixed_date",
        "relative_duration",
        "event_bound",
        "contingent",
        "retroactive",
        "source_checkpoint",
    }
)

# §3.1 — precedence classes.
PRECEDENCE_CLASSES: frozenset[str] = frozenset(
    {
        "temporary_over_permanent",
        "same_rail_latest",
        "source_order",
        "explicit_priority",
    }
)

# §4 — the three v0 profiles (clients may not invent profiles).
PROFILE_GOVERNING_TEXT = "lawvm.selection.governing_text.v1"
PROFILE_IN_FORCE_TEXT = "lawvm.selection.in_force_text.v1"
PROFILE_VIEWER_DEFAULT = "lawvm.selection.viewer_default.v1"
V0_PROFILE_IDS: tuple[str, ...] = (
    PROFILE_GOVERNING_TEXT,
    PROFILE_IN_FORCE_TEXT,
    PROFILE_VIEWER_DEFAULT,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

Interval = tuple[str, str | None]
"""Half-open ``[start, end_or_null]`` — end exclusive (OBJECT_MODEL §4)."""


def _interval(value: Interval, *, field_name: str) -> list[JsonValue]:
    """Validate + render a half-open interval to a JSON ``[start, end|null]``."""
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise SelectionError(
            f"{field_name} must be a 2-element [start, end_or_null] interval, "
            f"got {value!r}"
        )
    start, end = value
    if not isinstance(start, str) or not start:
        raise SelectionError(
            f"{field_name} start must be a non-empty token/date string, got {start!r}"
        )
    if end is not None and (not isinstance(end, str) or not end):
        raise SelectionError(
            f"{field_name} end must be null (open) or a non-empty token/date, got {end!r}"
        )
    if end is not None and end <= start:
        raise SelectionError(
            f"{field_name} is empty or inverted: end ({end!r}) must be > start ({start!r}) "
            f"(half-open, end exclusive)"
        )
    return [start, end]


def _str_list(values: Sequence[str], *, field_name: str) -> list[str]:
    out: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str):
            raise SelectionError(f"{field_name}[{index}] must be a string, got {value!r}")
        out.append(value)
    return out


# --------------------------------------------------------------------------- #
# §3.4 — ScopePredicate (closed, explicit, finite)                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ScopePredicate:
    """``lawvm.scope_predicate.v1`` — CLOSED at five dimensions (§3.4; design §21 #5).

    ``dimensions`` maps each of :data:`SCOPE_DIMENSIONS` to a sorted, deduped
    list of admitted values; an absent/empty dimension is the wildcard "any".
    ``status`` is one of :data:`SCOPE_PREDICATE_STATUSES`. A browser must never
    silently evaluate an ``unsupported`` predicate — that surfaces as ambiguity
    or a ``scope_predicate_unsupported`` block at selection time, never a guess.
    """

    dimensions: Mapping[str, Sequence[str]]
    status: str = "total"

    def __post_init__(self) -> None:
        if self.status not in SCOPE_PREDICATE_STATUSES:
            raise SelectionError(
                f"ScopePredicate.status must be one of {sorted(SCOPE_PREDICATE_STATUSES)!r}, "
                f"got {self.status!r}"
            )
        normalized: dict[str, tuple[str, ...]] = {}
        for key, raw in self.dimensions.items():
            if key not in SCOPE_DIMENSIONS:
                raise SelectionError(
                    f"ScopePredicate dimension {key!r} is not closed-set "
                    f"{SCOPE_DIMENSIONS!r}; an open/unsupported dimension must be "
                    f"declared via status='unsupported', never smuggled as a key "
                    f"(design §21 #5)"
                )
            if isinstance(raw, str):
                raise SelectionError(
                    f"ScopePredicate dimension {key!r} must be a list of values, not a bare string"
                )
            values = sorted({nfc(str(v)) for v in raw if str(v)})
            normalized[key] = tuple(values)
        object.__setattr__(self, "dimensions", normalized)

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        """Body sans id. Every closed dimension is present (empty = wildcard)."""
        return {
            "schema": _SCHEMA_SCOPE_PREDICATE,
            "dimensions": {dim: list(self.dimensions.get(dim, ())) for dim in SCOPE_DIMENSIONS},
            "status": self.status,
        }

    @property
    def scope_predicate_id(self) -> str:
        return leaf_hash(_DOMAIN_SCOPE_PREDICATE, self.to_canonical_dict())


# --------------------------------------------------------------------------- #
# §3.1 — ApplicabilityFact (engine-produced, the audited basis)              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TemporalBasis:
    """``temporal_basis`` block of an applicability fact (§3.1)."""

    kind: str
    event_refs: tuple[str, ...] = ()
    finding_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in TEMPORAL_BASIS_KINDS:
            raise SelectionError(
                f"TemporalBasis.kind must be one of {sorted(TEMPORAL_BASIS_KINDS)!r}, "
                f"got {self.kind!r}"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind,
            "event_refs": _str_list(self.event_refs, field_name="event_refs"),
            "finding_refs": _str_list(self.finding_refs, field_name="finding_refs"),
        }


@dataclass(frozen=True, slots=True)
class ApplicabilityFact:
    """``lawvm.applicability_fact.v1`` — engine-produced audited basis (§3.1).

    Facts alone are **not** enough for the browser to select — they are the
    basis the L1 checker verifies selection rows against.
    """

    work_id: str
    address_id: str
    node_version_id: str
    content_leaf_hash: str
    branch_id: str
    effect_interval: Interval
    enactment_interval: Interval
    account_interval: Interval
    rail: str
    scope_predicate_id: str
    precedence_class: str
    temporal_basis: TemporalBasis
    produced_by_transition_id: str
    residual_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.rail not in RAILS:
            raise SelectionError(
                f"ApplicabilityFact.rail must be one of {sorted(RAILS)!r}, got {self.rail!r}"
            )
        if self.precedence_class not in PRECEDENCE_CLASSES:
            raise SelectionError(
                f"ApplicabilityFact.precedence_class must be one of "
                f"{sorted(PRECEDENCE_CLASSES)!r}, got {self.precedence_class!r}"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_APPLICABILITY_FACT,
            "work_id": self.work_id,
            "address_id": self.address_id,
            "node_version_id": self.node_version_id,
            "content_leaf_hash": self.content_leaf_hash,
            "branch_id": self.branch_id,
            "effect_interval": _interval(self.effect_interval, field_name="effect_interval"),
            "enactment_interval": _interval(
                self.enactment_interval, field_name="enactment_interval"
            ),
            "account_interval": _interval(self.account_interval, field_name="account_interval"),
            "rail": self.rail,
            "scope_predicate_id": self.scope_predicate_id,
            "precedence_class": self.precedence_class,
            "temporal_basis": self.temporal_basis.to_canonical_dict(),
            "produced_by_transition_id": self.produced_by_transition_id,
            "residual_refs": _str_list(self.residual_refs, field_name="residual_refs"),
        }

    @property
    def fact_id(self) -> str:
        return leaf_hash(_DOMAIN_APPLICABILITY_FACT, self.to_canonical_dict())


# --------------------------------------------------------------------------- #
# §3.3 — SelectionCandidateSet (MANDATORY for every nontrivial row)          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SelectionCandidate:
    """One alternative considered for a selection cell (§3.3 ``candidates[]``)."""

    node_version_id: str
    rail: str
    effect_interval: Interval
    scope_predicate_id: str
    eligible: bool
    rejected_reason: str | None = None

    def __post_init__(self) -> None:
        if self.rail not in RAILS:
            raise SelectionError(
                f"SelectionCandidate.rail must be one of {sorted(RAILS)!r}, got {self.rail!r}"
            )
        if self.eligible and self.rejected_reason is not None:
            raise SelectionError(
                "SelectionCandidate cannot be eligible=True with a rejected_reason "
                "(an eligible candidate was not rejected)"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "node_version_id": self.node_version_id,
            "rail": self.rail,
            "effect_interval": _interval(self.effect_interval, field_name="effect_interval"),
            "scope_predicate_id": self.scope_predicate_id,
            "eligible": self.eligible,
            "rejected_reason": self.rejected_reason,
        }


@dataclass(frozen=True, slots=True)
class SelectionCandidateSet:
    """``lawvm.selection_candidate_set.v1`` — the auditable alternatives (§3.3).

    ``complete=true`` + the full candidate list turn a bare selection claim into
    an auditable decision. This is ``build_new`` (the seam's
    ``VersionSelectionTie`` does not provide a complete candidate set, §15.2).
    """

    selection_key: str
    candidates: tuple[SelectionCandidate, ...]
    complete: bool = True
    completion_basis: str = "derived_from_applicability_fact_root"

    def __post_init__(self) -> None:
        node_version_ids = [c.node_version_id for c in self.candidates]
        if len(set(node_version_ids)) != len(node_version_ids):
            raise SelectionError(
                "SelectionCandidateSet has duplicate node_version_id candidates; "
                "each alternative must appear once"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_CANDIDATE_SET,
            "selection_key": self.selection_key,
            "candidates": [c.to_canonical_dict() for c in self.candidates],
            "complete": self.complete,
            "completion_basis": self.completion_basis,
        }

    @property
    def candidate_set_id(self) -> str:
        return leaf_hash(_DOMAIN_CANDIDATE_SET, self.to_canonical_dict())


# --------------------------------------------------------------------------- #
# §3.2 — SelectionRow (the sparse public answer; replaces dense active_at)    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DecisionBasis:
    """``decision_basis`` block of a selection row (§3.2)."""

    selection_rule_id: str
    applicability_fact_refs: tuple[str, ...] = ()
    transition_refs: tuple[str, ...] = ()
    finding_refs: tuple[str, ...] = ()

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "selection_rule_id": self.selection_rule_id,
            "applicability_fact_refs": _str_list(
                self.applicability_fact_refs, field_name="applicability_fact_refs"
            ),
            "transition_refs": _str_list(self.transition_refs, field_name="transition_refs"),
            "finding_refs": _str_list(self.finding_refs, field_name="finding_refs"),
        }


@dataclass(frozen=True, slots=True)
class SelectionRow:
    """``lawvm.selection_row.v1`` — the sparse public answer (§3.2).

    Rows store **maximal intervals/cells where the selected result is constant**
    — never one row per date (§11 step 7). ``account_interval`` /
    ``source_policy_id`` scope the row to a ``corpus_version`` STRING token so a
    metadata-only republish bumps proof/source roots without invalidating
    legal-state selection (§10.7).

    Status discipline (§5): ``absent`` = no node version exists at this
    address/date/profile; ``out_of_scope`` = versions exist but none match the
    scope query (these are DISTINCT — keep both). ``block_reason`` is set iff
    ``status == "blocked"``. ``selected_node_version_id`` is set iff
    ``status == "selected"``.
    """

    work_id: str
    query_profile_id: str
    branch_id: str
    address_id: str
    scope_query_id: str
    effect_interval: Interval
    account_interval: Interval
    source_policy_id: str
    status: str
    candidate_set_hash: str | None = None
    selected_node_version_id: str | None = None
    required_scope_dimensions: tuple[str, ...] = ()
    block_reason: str | None = None
    decision_basis: DecisionBasis | None = None

    def __post_init__(self) -> None:
        if self.status not in SELECTION_STATUSES:
            raise SelectionError(
                f"SelectionRow.status must be one of {sorted(SELECTION_STATUSES)!r}, "
                f"got {self.status!r}"
            )
        if self.status == "selected" and not self.selected_node_version_id:
            raise SelectionError(
                "a 'selected' row must carry selected_node_version_id (§3.2)"
            )
        if self.status != "selected" and self.selected_node_version_id:
            raise SelectionError(
                f"a non-selected ({self.status!r}) row must not carry "
                f"selected_node_version_id (§5)"
            )
        if self.status == "blocked" and self.block_reason is None:
            raise SelectionError("a 'blocked' row must carry a block_reason (§5)")
        if self.status != "blocked" and self.block_reason is not None:
            raise SelectionError(
                f"block_reason is only valid when status=='blocked', got status={self.status!r} (§5)"
            )
        if self.block_reason is not None and self.block_reason not in BLOCK_REASONS:
            raise SelectionError(
                f"block_reason must be one of {sorted(BLOCK_REASONS)!r}, got {self.block_reason!r}"
            )
        if self.status == "ambiguous_missing_scope" and not self.required_scope_dimensions:
            raise SelectionError(
                "an 'ambiguous_missing_scope' row must name the required_scope_dimensions "
                "that disambiguate it (§3.4) — never 'prefer broader/latest'"
            )
        for dim in self.required_scope_dimensions:
            if dim not in SCOPE_DIMENSIONS:
                raise SelectionError(
                    f"required_scope_dimensions entry {dim!r} is not a closed scope dimension "
                    f"{SCOPE_DIMENSIONS!r}"
                )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_SELECTION_ROW,
            "work_id": self.work_id,
            "query_profile_id": self.query_profile_id,
            "branch_id": self.branch_id,
            "address_id": self.address_id,
            "scope_query_id": self.scope_query_id,
            "effect_interval": _interval(self.effect_interval, field_name="effect_interval"),
            "account_interval": _interval(self.account_interval, field_name="account_interval"),
            "source_policy_id": self.source_policy_id,
            "status": self.status,
            "selected_node_version_id": self.selected_node_version_id,
            "candidate_set_hash": self.candidate_set_hash,
            "required_scope_dimensions": list(self.required_scope_dimensions),
            "block_reason": self.block_reason,
            "decision_basis": (
                self.decision_basis.to_canonical_dict() if self.decision_basis is not None else None
            ),
        }

    @property
    def selection_key(self) -> str:
        """Content-addressed id of the row (the ``selection_key`` of §3.2).

        Keyed by the full query coordinate + the selected/blocked outcome, so a
        checker can detect a missing or surplus row against the universe (§6).
        """
        return leaf_hash(_DOMAIN_SELECTION_ROW, self.to_canonical_dict())


# --------------------------------------------------------------------------- #
# §4 — SelectionProfile (hashed contract; clients may not invent semantics)   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SelectionProfile:
    """``lawvm.selection_profile.v1`` — a hashed selection contract (§4).

    A profile fixes ``effect_date_axis``, ``account_axis``, ``branch_axis``,
    ``scope_policy``, ``contingency_policy``, ``retroactivity_policy``,
    ``ultra_activity_policy``. The v0 family mirrors the existing seam's
    ``governing`` vs ``in_force`` semantics lifted into named profiles (§4) —
    not ad-hoc viewer logic. Use :func:`v0_profiles` for the pinned three.
    """

    profile_id: str
    effect_date_axis: str
    account_axis: str
    branch_axis: str
    scope_policy: str
    contingency_policy: str
    retroactivity_policy: str
    ultra_activity_policy: str
    description: str = ""

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_SELECTION_PROFILE,
            "profile_id": self.profile_id,
            "effect_date_axis": self.effect_date_axis,
            "account_axis": self.account_axis,
            "branch_axis": self.branch_axis,
            "scope_policy": self.scope_policy,
            "contingency_policy": self.contingency_policy,
            "retroactivity_policy": self.retroactivity_policy,
            "ultra_activity_policy": self.ultra_activity_policy,
            "description": nfc(self.description),
        }

    @property
    def profile_content_id(self) -> str:
        """Content hash of the profile contract (distinct from the stable ``profile_id``).

        ``profile_id`` is the stable named handle (``lawvm.selection.*.v1``) that
        rows reference; this content id pins the *meaning* so a silently-changed
        contract is detectable under ``selection_profile_root``.
        """
        return leaf_hash(_DOMAIN_SELECTION_PROFILE, self.to_canonical_dict())


def v0_profiles() -> tuple[SelectionProfile, ...]:
    """The three pinned v0 profiles (§4). Reserve-only profiles are excluded.

    * ``governing_text`` — text effective at effect_date (``effective <= as_of``);
    * ``in_force_text`` — additionally gates on enactment/commencement
      (``enacted <= as_of``) and expiry (``expires > as_of``, exclusive);
    * ``viewer_default`` — governing_text on the actual branch under the latest
      account.
    """
    return (
        SelectionProfile(
            profile_id=PROFILE_GOVERNING_TEXT,
            effect_date_axis="effective_le_as_of",
            account_axis="explicit_account_version",
            branch_axis="explicit_branch",
            scope_policy="block_if_unresolved",
            contingency_policy="block_if_unresolved",
            retroactivity_policy="honor_relates_back_if_certified",
            ultra_activity_policy="not_modeled",
            description="text effective at effect_date (governing semantics)",
        ),
        SelectionProfile(
            profile_id=PROFILE_IN_FORCE_TEXT,
            effect_date_axis="effective_le_as_of",
            account_axis="explicit_account_version",
            branch_axis="explicit_branch",
            scope_policy="block_if_unresolved",
            contingency_policy="block_if_unresolved",
            retroactivity_policy="honor_relates_back_if_certified",
            ultra_activity_policy="not_modeled",
            description="additionally gates enacted<=as_of and expires>as_of (exclusive)",
        ),
        SelectionProfile(
            profile_id=PROFILE_VIEWER_DEFAULT,
            effect_date_axis="effective_le_as_of",
            account_axis="latest_account_version",
            branch_axis="actual",
            scope_policy="block_if_unresolved",
            contingency_policy="block_if_unresolved",
            retroactivity_policy="honor_relates_back_if_certified",
            ultra_activity_policy="not_modeled",
            description="public viewer default := governing_text on actual branch, latest account",
        ),
    )


# --------------------------------------------------------------------------- #
# §6 — SelectionUniverse (the keystone; makes omission detectable)            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SelectionUniverse:
    """``lawvm.selection_universe.v1`` — which questions are covered (§6).

    The highest-risk hash mistake is committing to the rows that *exist* without
    committing to the rows that *should* exist. ``selection_universe_root`` is a
    :func:`lawvm.substrate.roots.map_root` over the expected selection keys
    (``{selection_key: row_object_hash}``), so a checker enforces
    ``domain(rows) == domain(universe)`` — **both shortfall and surplus are
    invalid** (§6).
    """

    work_id: str
    query_profile_ids: tuple[str, ...]
    branch_ids: tuple[str, ...]
    expected_selection_keys: Mapping[str, str]
    address_root: str
    effect_boundary_root: str
    account_boundary_root: str
    scope_query_root: str

    def __post_init__(self) -> None:
        keys = list(self.expected_selection_keys)
        if len(set(keys)) != len(keys):
            raise SelectionError("SelectionUniverse has duplicate expected selection keys")

    @property
    def selection_key_root(self) -> str:
        """``MapRoot`` over ``{selection_key: row_object_hash}`` (the keystone, §6).

        Empty universe is a valid deterministic root (a stub/unprocessed work):
        ``map_root`` over ``{}`` is well-defined. Adding, dropping, or renaming a
        key changes the root — that is what makes omission detectable.
        """
        return map_root("selection_universe", dict(self.expected_selection_keys))

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_SELECTION_UNIVERSE,
            "work_id": self.work_id,
            "query_profile_ids": list(self.query_profile_ids),
            "branch_ids": list(self.branch_ids),
            "address_root": self.address_root,
            "effect_boundary_root": self.effect_boundary_root,
            "account_boundary_root": self.account_boundary_root,
            "scope_query_root": self.scope_query_root,
            "selection_key_root": self.selection_key_root,
        }

    @property
    def universe_id(self) -> str:
        return leaf_hash(_DOMAIN_SELECTION_UNIVERSE, self.to_canonical_dict())


# --------------------------------------------------------------------------- #
# §7 — The StateSelectionIndex multi-root                                     #
# --------------------------------------------------------------------------- #

# The eight selection sub-roots (§7) over which ``state_selection_root`` is a
# SetRoot. The empty-tail sub-roots (events/residuals/registry) are carried so
# omission of a whole sub-layer is detectable; an empty layer is a valid
# SetRoot over zero leaves.
STATE_SELECTION_SUBROOT_NAMES: tuple[str, ...] = (
    "selection_profile_root",
    "selection_universe_root",
    "scope_predicate_root",
    "applicability_fact_root",
    "candidate_set_root",
    "selection_row_root",
    "temporal_event_root",
    "temporal_residual_root",
)


def _object_root(domain: str, object_hashes: Sequence[str]) -> str:
    """SetRoot over a collection of object hashes (empty = valid empty SetRoot)."""
    return set_root(domain, object_hashes)


@dataclass(frozen=True, slots=True)
class StateSelectionRoots:
    """The eight ``state_selection_root`` children + the combined multi-root (§7).

    ``state_selection_root = set_root(...)`` over the eight named sub-roots.
    This is **one of the four children** of ``selection_index_root`` (the others
    being ``content_leaf_root``, ``node_version_root``, ``projection_root`` —
    OBJECT_MODEL §2.4); it is NOT ``selection_index_root`` itself. The two are
    never conflated (spec §7 "frozen — two distinct roots").
    """

    selection_profile_root: str
    selection_universe_root: str
    scope_predicate_root: str
    applicability_fact_root: str
    candidate_set_root: str
    selection_row_root: str
    temporal_event_root: str
    temporal_residual_root: str

    def subroots(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in STATE_SELECTION_SUBROOT_NAMES}

    @property
    def state_selection_root(self) -> str:
        """``SetRoot`` over the eight sub-roots (§7). Duplicate sub-root → RootError.

        A SetRoot over the sub-root *values* (not a MapRoot over names): the
        eight names are a fixed structural schema, so the set of their hash
        values is the commitment. Sorting + dedup is handled by ``set_root``.
        """
        return set_root("state_selection", self.subroots().values())


def build_state_selection_roots(
    *,
    selection_profile_object_hashes: Sequence[str],
    selection_universe_object_hashes: Sequence[str],
    scope_predicate_object_hashes: Sequence[str],
    applicability_fact_object_hashes: Sequence[str],
    candidate_set_object_hashes: Sequence[str],
    selection_row_object_hashes: Sequence[str],
    temporal_event_object_hashes: Sequence[str] = (),
    temporal_residual_object_hashes: Sequence[str] = (),
) -> StateSelectionRoots:
    """Build the eight sub-roots (each a ``SetRoot`` over its object hashes, §7).

    Empty layers (e.g. ``temporal_event_object_hashes`` in v0) yield a valid
    empty ``SetRoot`` — omission of the whole layer is still committed to.
    """
    return StateSelectionRoots(
        selection_profile_root=_object_root("selection_profile", selection_profile_object_hashes),
        selection_universe_root=_object_root(
            "selection_universe", selection_universe_object_hashes
        ),
        scope_predicate_root=_object_root("scope_predicate", scope_predicate_object_hashes),
        applicability_fact_root=_object_root(
            "applicability_fact", applicability_fact_object_hashes
        ),
        candidate_set_root=_object_root("candidate_set", candidate_set_object_hashes),
        selection_row_root=_object_root("selection_row", selection_row_object_hashes),
        temporal_event_root=_object_root("temporal_event", temporal_event_object_hashes),
        temporal_residual_root=_object_root(
            "temporal_residual", temporal_residual_object_hashes
        ),
    )


@dataclass(frozen=True, slots=True)
class SelectionIndexRoots:
    """The MANIFEST-level ``selection_index_root`` + its four children (§7; OBJECT_MODEL §2.4).

    ``selection_index_root = set_root(...)`` over exactly four children:
    ``{content_leaf_root, node_version_root, state_selection_root,
    projection_root}``. ``content_leaf_root`` / ``node_version_root`` /
    ``projection_root`` are SetRoots owned by the broader object model (passed
    in here); ``state_selection_root`` is the multi-root this module owns.
    """

    content_leaf_root: str
    node_version_root: str
    state_selection_root: str
    projection_root: str

    def children(self) -> dict[str, str]:
        return {
            "content_leaf_root": self.content_leaf_root,
            "node_version_root": self.node_version_root,
            "state_selection_root": self.state_selection_root,
            "projection_root": self.projection_root,
        }

    @property
    def selection_index_root(self) -> str:
        """``SetRoot`` over the four children (§2.4). Duplicate child → RootError."""
        return set_root("selection_index", self.children().values())


def build_selection_index_roots(
    *,
    content_leaf_root: str,
    node_version_root: str,
    state_selection_root: str,
    projection_root: str,
) -> SelectionIndexRoots:
    """Assemble the manifest-level ``selection_index_root`` children (§2.4)."""
    return SelectionIndexRoots(
        content_leaf_root=content_leaf_root,
        node_version_root=node_version_root,
        state_selection_root=state_selection_root,
        projection_root=projection_root,
    )
