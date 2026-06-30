"""Executable mirror of the TLA+ temporal-overlay selection invariants.

``proofs/tla/LawVMTemporalOverlay.tla`` states safety invariants about the
two-rail (temporary overlay vs permanent background) point-in-time selector.
The TLA+ model is a *separate* hand-written abstraction: nothing forces it to
track the real Python selector, and (as of this writing) the ``.tla`` was last
edited at the v0.1 release while ``timeline_selection.py`` has changed many
times since.  TLC checks the model against itself; it does NOT check the model
against the code.

This module is the missing executable bridge for the highest-value selection
invariants.  It re-states them directly against the REAL selector
``select_active_version_ex`` over a Hypothesis-generated version lattice, so the
property is asserted on production code and re-checked on every CI run.  When
the selector semantics drift, THIS test moves — that is the point.

Mirrored TLA+ invariants
------------------------
- ``Inv_TwoRailSelection`` — when any temporary (overlay) version is eligible at
  the query date, the selected version is a temporary one; when none is, the
  selection is the background (permanent) pick.  See ``TempIdx`` / ``BgIdx`` /
  ``SelectedIdx`` in the spec, and ``select_active_version_ex_prevalidated`` in
  ``timeline_selection.py``.
- ``Inv_InForceOnlyUsesEnacted`` — under ``query_type="in_force"`` a selected
  version always has ``enacted <= as_of``.  See ``Eligible`` /
  ``Inv_InForceOnlyUsesEnacted`` in the spec, and the ``in_force`` branch of
  ``eligible()`` in the code.
- A bounded-eligibility consequence of ``Inv_NoBackgroundNoOverlayMeansAbsent``:
  if no version is eligible at the query date, the selection status is
  ``absent``.
- The ``Eligible`` gate (Z3 ``P1``/``P2`` analogue): any selected version is
  eligible — never expired, never future.

Scope note (semantics the model does NOT cover, deliberately NOT asserted here)
-----------------------------------------------------------------------------
The real selector additionally honours an ``expires_as_of`` shared-sunset
horizon and a regime-handoff lex-posterior rule (``_independent_later_
background_supersedes_overlay`` and the last-in-force-day handoff in
``select_active_version_ex_prevalidated``).  The TLA+ model has neither.  To
keep this an honest mirror of the MODELLED two-rail rule, the generated lattice
uses the default ``expires_as_of`` (so ``expires`` is compared against
``as_of``) and single-source versions (so the lex-posterior handoff never
fires).  Those unmodelled behaviours are documented in the audit note
``notes/TLA_SPEC_DRIFT_AUDIT_2026-06-30.md`` and are NOT drift in the mirror.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from hypothesis import given, settings
from hypothesis import strategies as st

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    ProvisionTimeline,
    ProvisionVersion,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline_selection import (
    eligible,
    select_active_version_ex,
)

_ADDR = LegalAddress(path=(("section", "1"),))
# Small finite date lattice mirroring the TLA+ ``Dates == 0..MaxDate`` ladder.
_DATES = [f"2020-01-0{i}" for i in range(1, 6)]


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label="1", text=text)


@st.composite
def _version(draw: st.DrawFn) -> ProvisionVersion:
    """One modelled version: permanent/temporary, effective/enacted/expires.

    Constrained to the modelled fragment: single content node, no applicability
    scoping, ``enacted`` drawn independently (the model permits dormant
    ``enacted < effective`` versions), ``expires`` strictly after ``effective``
    when present (matching ``Inv_TemporaryWellFormed`` /
    ``ProvisionVersion.__post_init__``).
    """
    variant: Literal["permanent", "temporary"] = draw(
        st.sampled_from(["permanent", "temporary"])
    )
    effective = draw(st.sampled_from(_DATES))
    enacted = draw(st.sampled_from(_DATES))
    later = [d for d in _DATES if d > effective]
    if variant == "temporary":
        # Temporary versions are well-formed only with an expires strictly after
        # effective (TLA+ Inv_TemporaryWellFormed; code rejects expires<effective).
        if not later:
            # No room for a valid temporary expiry at the top of the ladder:
            # fall back to a permanent version rather than an invalid temporary.
            variant = "permanent"
            expires = ""
        else:
            expires = draw(st.sampled_from(later))
    else:
        expires = draw(st.sampled_from([""] + later))
    return ProvisionVersion(
        effective=effective,
        enacted=enacted,
        expires=expires,
        variant_kind=variant,
        content=_content(f"{variant}-{effective}-{enacted}-{expires or 'inf'}"),
    )


@st.composite
def _timeline(draw: st.DrawFn) -> ProvisionTimeline:
    versions = draw(st.lists(_version(), min_size=1, max_size=5))
    return ProvisionTimeline(address=_ADDR, versions=versions)


def _eligible_versions(
    tl: ProvisionTimeline, as_of: str, query_type: str
) -> list[ProvisionVersion]:
    return [v for v in tl.versions if eligible(v, as_of, query_type)]


def _day_before(iso_date: str) -> str:
    return (dt.date.fromisoformat(iso_date) - dt.timedelta(days=1)).isoformat()


def _regime_handoff_day_active(
    eligibles: list[ProvisionVersion], as_of: str
) -> bool:
    """Whether the real selector's POST-v0.1 lex-posterior carve-out can fire.

    The TLA+ model's ``Inv_TwoRailSelection`` says an eligible temporary overlay
    is ALWAYS selected over the background.  The real
    ``select_active_version_ex_prevalidated`` adds a regime-handoff exception the
    model does NOT capture: on the overlay's LAST in-force day, a permanent
    background whose ``effective`` is later than the overlay's and falls on/after
    that day (``background.effective >= overlay.expires - 1``) is lex posterior
    and wins.  This helper reproduces that exact predicate so the mirror asserts
    the REAL two-rail rule (model invariant minus the documented carve-out)
    rather than a rule the code no longer implements.  See the audit note
    ``notes/TLA_SPEC_DRIFT_AUDIT_2026-06-30.md`` (Inv_TwoRailSelection row).
    """
    overlays = [v for v in eligibles if v.variant_kind == "temporary" and v.expires]
    backgrounds = [v for v in eligibles if v.variant_kind == "permanent"]
    for overlay in overlays:
        handoff_day = _day_before(overlay.expires)
        for background in backgrounds:
            if (
                background.effective > overlay.effective
                and background.effective >= handoff_day
            ):
                return True
    return False


@settings(max_examples=400, deadline=None)
@given(_timeline(), st.sampled_from(_DATES), st.sampled_from(["governing", "in_force"]))
def test_inv_two_rail_selection_against_real_selector(
    tl: ProvisionTimeline, as_of: str, query_type: str
) -> None:
    """Mirror of TLA+ ``Inv_TwoRailSelection`` on ``select_active_version_ex``.

    If any temporary version is eligible at ``as_of`` then the selected version
    (when one is selected) is temporary; if no temporary is eligible then the
    selected version (when one is selected) is permanent.

    DRIFT CARVE-OUT: the real selector adds a regime-handoff lex-posterior rule
    (a newer background on the overlay's last in-force day supersedes the
    overlay) that the TLA+ model lacks.  We exclude exactly those cases via
    ``_regime_handoff_day_active`` so the mirror tracks the code's real
    behaviour; the carve-out's existence is the recorded model-incompleteness
    (see ``notes/TLA_SPEC_DRIFT_AUDIT_2026-06-30.md``).
    """
    result = select_active_version_ex(tl, as_of, query_type=query_type)
    if result.selection_status != "selected":
        return
    version = result.version
    assert version is not None
    eligibles = _eligible_versions(tl, as_of, query_type)
    any_temp_eligible = any(v.variant_kind == "temporary" for v in eligibles)
    if any_temp_eligible:
        if _regime_handoff_day_active(eligibles, as_of):
            # Documented model-incomplete exception: lex-posterior background
            # may win on the handoff day. The selector must still return one of
            # the two rails, never a non-eligible version.
            assert version in eligibles
            return
        assert version.variant_kind == "temporary", (
            "Inv_TwoRailSelection: an eligible temporary overlay exists but the "
            f"selector returned a {version.variant_kind} version at {as_of}"
        )
    else:
        assert version.variant_kind == "permanent", (
            "Inv_TwoRailSelection: no eligible temporary overlay exists but the "
            f"selector returned a {version.variant_kind} version at {as_of}"
        )


@settings(max_examples=400, deadline=None)
@given(_timeline(), st.sampled_from(_DATES))
def test_inv_in_force_only_uses_enacted_against_real_selector(
    tl: ProvisionTimeline, as_of: str
) -> None:
    """Mirror of TLA+ ``Inv_InForceOnlyUsesEnacted``.

    Any version selected under ``query_type="in_force"`` has ``enacted <= as_of``.
    A version with an empty ``enacted`` is treated by the code as ungated; the
    generator always sets ``enacted`` from the date ladder, so this also
    exercises the real comparison.
    """
    result = select_active_version_ex(tl, as_of, query_type="in_force")
    if result.selection_status != "selected":
        return
    version = result.version
    assert version is not None
    assert (not version.enacted) or version.enacted <= as_of, (
        "Inv_InForceOnlyUsesEnacted: in_force selected a version enacted "
        f"{version.enacted!r} after as_of {as_of!r}"
    )


@settings(max_examples=400, deadline=None)
@given(_timeline(), st.sampled_from(_DATES), st.sampled_from(["governing", "in_force"]))
def test_no_eligible_version_means_absent_against_real_selector(
    tl: ProvisionTimeline, as_of: str, query_type: str
) -> None:
    """Bounded consequence of TLA+ ``Inv_NoBackgroundNoOverlayMeansAbsent``.

    When no version is eligible at ``as_of`` the selection status is ``absent``
    (never a stale or future pick).  The converse — that an eligible version is
    always selected — does NOT hold in general (e.g. ``ambiguous_missing_scope``
    or territory-gated versions), so this asserts only the absent direction.
    """
    if _eligible_versions(tl, as_of, query_type):
        return
    result = select_active_version_ex(tl, as_of, query_type=query_type)
    assert result.selection_status == "absent", (
        "no eligible version at as_of must yield status=absent, got "
        f"{result.selection_status!r}"
    )
    assert result.version is None


@settings(max_examples=400, deadline=None)
@given(_timeline(), st.sampled_from(_DATES), st.sampled_from(["governing", "in_force"]))
def test_selected_version_is_eligible_against_real_selector(
    tl: ProvisionTimeline, as_of: str, query_type: str
) -> None:
    """Cross-check (TLA+ ``Eligible`` gate): any selected version is eligible.

    Mirrors the Z3 ``P1``/``P2`` no-expired/no-future selector proofs, but
    against the REAL ``select_active_version_ex`` instead of an abstract Z3
    selector function.
    """
    result = select_active_version_ex(tl, as_of, query_type=query_type)
    if result.selection_status != "selected":
        return
    version = result.version
    assert version is not None
    assert eligible(version, as_of, query_type), (
        f"selected version (effective={version.effective}, expires={version.expires}, "
        f"enacted={version.enacted}, {version.variant_kind}) is not eligible at "
        f"{as_of} under {query_type}"
    )
    # No-future (Z3 P2): effective never after as_of.
    assert version.effective <= as_of
    # No-expired (Z3 P1): an expiry, if present, is strictly after as_of.
    assert (not version.expires) or version.expires > as_of


def _iso(date: dt.date) -> str:
    return date.isoformat()


def test_mirror_smoke_two_rail_overlay_wins() -> None:
    """Concrete anchor: an eligible temporary overlay wins over a permanent.

    Pins the ``Inv_TwoRailSelection`` overlay-wins direction with a hand-built
    case so a regression is legible even if the generator is later narrowed.
    """
    base = ProvisionVersion(
        effective="2020-01-01",
        enacted="2020-01-01",
        variant_kind="permanent",
        content=_content("permanent"),
    )
    overlay = ProvisionVersion(
        effective="2020-01-02",
        enacted="2020-01-02",
        expires="2020-01-04",
        variant_kind="temporary",
        content=_content("temporary"),
    )
    tl = ProvisionTimeline(address=_ADDR, versions=[base, overlay])
    selected = select_active_version_ex(tl, "2020-01-03").version
    assert selected is not None
    assert selected.variant_kind == "temporary"
    # On the overlay's exclusive expiry date the background rail is selected.
    after = select_active_version_ex(tl, "2020-01-04").version
    assert after is not None
    assert after.variant_kind == "permanent"
