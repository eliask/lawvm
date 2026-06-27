"""Tests for the SE agreement-residual projector (sibling of the FI one).

See `src/lawvm/sweden/se_agreement_residuals.py`. Two responsibilities:

1. Closed-voltage guard: every classification string the live SE replay engine
   can emit is in `_SE_CLASSIFICATION_FAMILY_TABLE`, so a new row class added
   to `check_se_official_replay` cannot silently drop from the residual ledger.
   Mirrors the FI spec_ledger catalog anti-drift pattern.

2. Projection correctness: residual_id is stable (rerun-deterministic); family
   and status are within the shared AgreementResidualFamily/Status closed
   vocabularies; replay-derivation round-trips through the projection row.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lawvm.core.agreement_residual import (
    AgreementResidual,
    _VALID_FAMILIES,
    _VALID_STATUSES,
)
from lawvm.sweden.se_agreement_residuals import (
    SE_REPLAY_ROW_CLASSIFICATIONS,
    se_replay_agreement_residuals,
    se_replay_row_agreement_residual,
)

# Aggregated closed set of strings the replay engine writes to a row's
# ``classification`` field. Discovery is by literal scan over src/lawvm/sweden/fetch.py
# matched against any classification string returned by ``_official_oracle_classification``
# or assigned as a literal ``classification = "..."``. Mirrors the FI spec_ledger
# discovery pattern.
_SWEDEN_FETCH = Path(__file__).resolve().parents[1] / "src" / "lawvm" / "sweden" / "fetch.py"
_REPLAY_CLASSIFICATION_RE = re.compile(r'"((?:exact|table_rows_match|editorial_attribution_only|inline_numbering_only|official_oracle_version_mismatch|official_oracle_match_current_surface_drift|official_oracle_match_missing_current_post|official_oracle_match_version_unknown|repeal_stub_oracle_only|repeal_then_later_replaced_oracle_only|genuine_mismatch))"')


def _live_classification_strings() -> set[str]:
    """Static set of classification literals emitted by ``check_se_official_replay``
    plus its classification helpers in src/lawvm/sweden/fetch.py."""
    src = _SWEDEN_FETCH.read_text(encoding="utf-8")
    return set(_REPLAY_CLASSIFICATION_RE.findall(src))


def _row(classification: str, *, section: str = "5", match: bool = True, replay_text: str = "a", post_text: str = "a") -> dict[str, object]:
    return {
        "classification": classification,
        "section": section,
        "match": match,
        "replay_text": replay_text,
        "post_text": post_text,
    }


# --------------------------------------------------------------------------- #
# Closed-vocabulary guard.                                                    #
# --------------------------------------------------------------------------- #


def test_family_table_covers_every_live_classification() -> None:
    """Anti-drift: every classification string the replay engine emits is in the
    family table. Adding a new row class MUST register a family mapping."""
    live = _live_classification_strings()
    assert live, "discovery found no live SE replay classification strings"
    missing = live - SE_REPLAY_ROW_CLASSIFICATIONS
    assert not missing, (
        f"{len(missing)} SE replay classification(s) lack a family mapping in "
        f"_SE_CLASSIFICATION_FAMILY_TABLE (projector coverage < 100%): {sorted(missing)}"
    )


def test_no_dead_family_table_entries() -> None:
    """Symmetric parity: every family-table entry must map to a live emit string.
    A dead entry is one for a classification fetch.py no longer produces."""
    live = _live_classification_strings()
    dead = SE_REPLAY_ROW_CLASSIFICATIONS - live
    assert not dead, (
        f"{len(dead)} _SE_CLASSIFICATION_FAMILY_TABLE entry/entries do not map to any "
        f"live SE replay classification string (stale entries): {sorted(dead)}"
    )


def test_family_table_values_are_closed_vocabulary() -> None:
    """Each family/status pair must lie inside the shared AgreementResidualFamily /
    AgreementResidualStatus closed vocabularies."""
    from lawvm.sweden.se_agreement_residuals import _SE_CLASSIFICATION_FAMILY_TABLE

    bad_family = []
    bad_status = []
    for classifier, (family, status, *_rest) in _SE_CLASSIFICATION_FAMILY_TABLE.items():
        if family not in _VALID_FAMILIES:
            bad_family.append((classifier, family))
        if status not in _VALID_STATUSES:
            bad_status.append((classifier, status))
    assert not bad_family, f"family not in closed AgreementResidualFamily: {bad_family}"
    assert not bad_status, f"status not in closed AgreementResidualStatus: {bad_status}"


def test_unknown_classification_raises_loud() -> None:
    """§1.10 fail-loud: a classification not in the closed table must raise, not
    silently drop the residual."""
    with pytest.raises(KeyError, match="no entry in _SE_CLASSIFICATION_FAMILY_TABLE"):
        se_replay_row_agreement_residual(
            _row("unknown_future_classification"), amending_sfs_id="2026:1", base_sfs_id="2026:2"
        )


# --------------------------------------------------------------------------- #
# Projection correctness.                                                     #
# --------------------------------------------------------------------------- #


def test_residual_id_is_stable_across_reruns() -> None:
    row = _row("exact", section="3", replay_text="Foo", post_text="Foo")
    a = se_replay_row_agreement_residual(row, amending_sfs_id="2026:1", base_sfs_id="2026:2")
    b = se_replay_row_agreement_residual(row, amending_sfs_id="2026:1", base_sfs_id="2026:2")
    assert a.residual_id == b.residual_id
    assert a.residual_id.startswith("sha256:")


def test_residual_id_distinguishes_distinct_text_head() -> None:
    """Two rows that share (sfs, base, section, classification) but materially
    different replay text must produce distinct residual_ids — the projector
    can't collapse two distinct rows into one residual."""
    same_args = dict(amending_sfs_id="2026:1", base_sfs_id="2026:2")
    a = se_replay_row_agreement_residual(_row("exact", section="3", replay_text="AAA", post_text="AAA"), **same_args)
    b = se_replay_row_agreement_residual(_row("exact", section="3", replay_text="BBB", post_text="BBB"), **same_args)
    assert a.residual_id != b.residual_id


def test_residual_id_distinguishes_distinct_classification() -> None:
    """Two rows with the same text but DIFFERENT classification must produce
    distinct residual_ids — the family contribution is part of identity."""
    same_args = dict(amending_sfs_id="2026:1", base_sfs_id="2026:2")
    a = se_replay_row_agreement_residual(_row("exact", section="3", replay_text="X", post_text="X"), **same_args)
    b = se_replay_row_agreement_residual(
        _row("official_oracle_version_mismatch", section="3", replay_text="X", post_text="X"), **same_args
    )
    assert a.residual_id != b.residual_id


def test_genuine_match_rows_carry_no_missing_proofs() -> None:
    """Agreement rows are provable — ``missing_proofs`` is empty by contract."""
    agreement_classifications = {"exact", "table_rows_match", "editorial_attribution_only", "inline_numbering_only", "repeal_stub_oracle_only"}
    for c in agreement_classifications:
        r = se_replay_row_agreement_residual(_row(c), amending_sfs_id="2026:1", base_sfs_id="2026:2")
        assert r.missing_proofs == (), f"{c!r} should have no missing_proofs; got {r.missing_proofs}"


def test_frontier_rows_carry_non_empty_missing_proofs() -> None:
    """Frontier/residual rows MUST name the proof they await — a frontier with
    no missing_proofs is folklore, not a typed residual."""
    frontier_classifications = {
        "official_oracle_version_mismatch",
        "repeal_then_later_replaced_oracle_only",
        "official_oracle_match_missing_current_post",
        "official_oracle_match_version_unknown",
        "official_oracle_match_current_surface_drift",
        "genuine_mismatch",
    }
    for c in frontier_classifications:
        r = se_replay_row_agreement_residual(_row(c, match=False), amending_sfs_id="2026:1", base_sfs_id="2026:2")
        assert r.missing_proofs, (
            f"{c!r} is status={r.agreement_residual_status!r} (frontier/residual) but has empty missing_proofs — a frontier with no revisit condition is folklore."
        )


def test_forbidden_shortcuts_block_authority_promotion() -> None:
    """§1.11/§1.12: a residual never authorizes replay. The forbidden_shortcuts
    must include the silent-authority-promotion paths."""
    r = se_replay_row_agreement_residual(_row("official_oracle_version_mismatch"), amending_sfs_id="2026:1", base_sfs_id="2026:2")
    assert "reclassify_to_agreement_via_projection_mutation" in r.forbidden_shortcuts
    assert "derive_replay_authority_from_residual_family" in r.forbidden_shortcuts


def test_se_replay_agreement_residuals_returns_one_per_row() -> None:
    """The projection over an entire replay result yields exactly one residual
    per row — every input row is owned (totality)."""
    replay = {
        "amending_sfs_id": "2026:1",
        "base_sfs_id": "2026:2",
        "rows": [_row("exact", section="1"), _row("official_oracle_version_mismatch", section="2", match=False)],
    }
    residuals = se_replay_agreement_residuals(replay)
    assert len(residuals) == 2
    assert all(isinstance(r, AgreementResidual) for r in residuals)
    assert residuals[0].residual_id != residuals[1].residual_id
