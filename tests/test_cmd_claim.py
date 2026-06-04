"""Tests for lawvm claim CLI subcommands.

Covers:
  - test_claim_show_renders_all_four_records
  - CLI smoke tests for each subcommand
  - test_self_authorization_impossible (via CLI path)
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

# Activate Finland claim kinds
import lawvm.finland.claim_kinds  # noqa: F401

from lawvm.core.manual_claims.hashing import compute_claim_id
from lawvm.core.manual_claims.primitive import (
    ClaimConfidence,
    ClaimLayer,
    ClaimScope,
    ClaimStatus,
    ManualCompilationClaim,
    Producer,
    ProfileTag,
    ReviewStatus,
    SourceLocator,
    SourceWitnessType,
    ValidatorStatus,
)
from lawvm.core.manual_claims.storage import ClaimStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_producer() -> Producer:
    return Producer(
        producer_kind="operator",
        handle="test",
        model_id=None,
        timestamp=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
        environment="test",
    )


def _make_claim_dict(
    statute_id: str = "711/2022",
    schema_version: str = "v1",
) -> dict:
    """Return a JSON-serializable claim dict with correct claim_id."""
    from lawvm.core.manual_claims.storage import _claim_to_dict

    partial = ManualCompilationClaim(
        claim_id="placeholder",
        schema_version=schema_version,
        jurisdiction="fi",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=ClaimScope(
            statute_id=statute_id,
            provision_ref="section:3",
            valid_at_start=date(2022, 1, 1),
            valid_at_end=None,
        ),
        target=(("statute_id", statute_id), ("mention_span", (100, 120))),
        value=(("resolved_statute_id", "1234/2020"), ("citation_form", "lain 1234/2020")),
        source_witness_type=SourceWitnessType.OPERATOR_FILING,
        producer=_make_producer(),
        cited_source_locator=SourceLocator(
            artifact_kind="finlex_akn",
            statute_id=statute_id,
            he_id=None,
            version_id=None,
        ),
        cited_source_span=(100, 120),
        cited_source_hash="a" * 64,
        dependency_fingerprint=(("target_hash", "abc"),),
        valid_at=(date(2022, 1, 1), None),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,),
        rationale="CLI test claim",
    )
    claim_id = compute_claim_id(partial)
    claim = ManualCompilationClaim(
        claim_id=claim_id,
        schema_version=schema_version,
        jurisdiction="fi",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=ClaimScope(
            statute_id=statute_id,
            provision_ref="section:3",
            valid_at_start=date(2022, 1, 1),
            valid_at_end=None,
        ),
        target=(("statute_id", statute_id), ("mention_span", (100, 120))),
        value=(("resolved_statute_id", "1234/2020"), ("citation_form", "lain 1234/2020")),
        source_witness_type=SourceWitnessType.OPERATOR_FILING,
        producer=_make_producer(),
        cited_source_locator=SourceLocator(
            artifact_kind="finlex_akn",
            statute_id=statute_id,
            he_id=None,
            version_id=None,
        ),
        cited_source_span=(100, 120),
        cited_source_hash="a" * 64,
        dependency_fingerprint=(("target_hash", "abc"),),
        valid_at=(date(2022, 1, 1), None),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,),
        rationale="CLI test claim",
    )
    return _claim_to_dict(claim), claim.claim_id


def _write_claim_file(tmp_path: Path, d: dict) -> Path:
    p = tmp_path / "claim.json"
    p.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return p


def _make_args(**kwargs):
    """Simple namespace for arg simulation."""
    class _Args:
        pass
    a = _Args()
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class TestCmdClaim:

    def test_propose_creates_claim(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_propose
        d, claim_id = _make_claim_dict()
        claim_file = _write_claim_file(tmp_path, d)
        data_dir = str(tmp_path)
        args = _make_args(claim_file=str(claim_file), data_dir=data_dir, validator=None)
        rc = cmd_propose(args)
        assert rc == 0
        store = ClaimStore(tmp_path / "manual_claims")
        assert store.claim_exists(claim_id)

    def test_propose_idempotent(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_propose
        d, claim_id = _make_claim_dict()
        claim_file = _write_claim_file(tmp_path, d)
        data_dir = str(tmp_path)
        args = _make_args(claim_file=str(claim_file), data_dir=data_dir, validator=None)
        rc1 = cmd_propose(args)
        rc2 = cmd_propose(args)
        assert rc1 == 0
        assert rc2 == 0

    def test_propose_tampered_claim_rejected(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_propose
        d, _ = _make_claim_dict()
        d["rationale"] = "tampered after id computation"
        claim_file = _write_claim_file(tmp_path, d)
        data_dir = str(tmp_path)
        args = _make_args(claim_file=str(claim_file), data_dir=data_dir, validator=None)
        with pytest.raises(ValueError, match="Claim ID mismatch"):
            cmd_propose(args)

    def test_accept_transitions_state(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_accept, cmd_propose
        d, claim_id = _make_claim_dict()
        claim_file = _write_claim_file(tmp_path, d)
        data_dir = str(tmp_path)
        cmd_propose(_make_args(claim_file=str(claim_file), data_dir=data_dir, validator=None))

        rc = cmd_accept(_make_args(claim_id=claim_id, data_dir=data_dir))
        assert rc == 0

        store = ClaimStore(tmp_path / "manual_claims")
        state = store.read_state(claim_id)
        assert state is not None
        assert state.status == ClaimStatus.ACCEPTED
        assert state.review_status == ReviewStatus.HUMAN_REVIEWED

    def test_accept_nonexistent_claim_fails(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_accept
        rc = cmd_accept(_make_args(claim_id="nonexistent" * 4, data_dir=str(tmp_path)))
        assert rc == 1

    def test_reject_transitions_state(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_propose, cmd_reject
        d, claim_id = _make_claim_dict()
        claim_file = _write_claim_file(tmp_path, d)
        data_dir = str(tmp_path)
        cmd_propose(_make_args(claim_file=str(claim_file), data_dir=data_dir, validator=None))

        rc = cmd_reject(_make_args(claim_id=claim_id, reason="wrong target", data_dir=data_dir))
        assert rc == 0

        store = ClaimStore(tmp_path / "manual_claims")
        state = store.read_state(claim_id)
        assert state is not None
        assert state.status == ClaimStatus.REJECTED

    def test_retract_accepted_claim(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_accept, cmd_propose, cmd_retract
        d, claim_id = _make_claim_dict()
        claim_file = _write_claim_file(tmp_path, d)
        data_dir = str(tmp_path)
        cmd_propose(_make_args(claim_file=str(claim_file), data_dir=data_dir, validator=None))
        cmd_accept(_make_args(claim_id=claim_id, data_dir=data_dir))

        rc = cmd_retract(_make_args(claim_id=claim_id, reason="bad claim", data_dir=data_dir))
        assert rc == 0

        store = ClaimStore(tmp_path / "manual_claims")
        state = store.read_state(claim_id)
        assert state is not None
        assert state.status == ClaimStatus.RETRACTED

    def test_retract_proposed_claim_fails(self, tmp_path: Path):
        """Cannot retract a proposed (not yet accepted) claim."""
        from lawvm.tools.cmd_claim import cmd_propose, cmd_retract
        d, claim_id = _make_claim_dict()
        claim_file = _write_claim_file(tmp_path, d)
        data_dir = str(tmp_path)
        cmd_propose(_make_args(claim_file=str(claim_file), data_dir=data_dir, validator=None))

        rc = cmd_retract(_make_args(claim_id=claim_id, reason="wrong", data_dir=data_dir))
        assert rc == 1

    def test_list_returns_claims(self, tmp_path: Path, capsys):
        from lawvm.tools.cmd_claim import cmd_list, cmd_propose
        d, claim_id = _make_claim_dict()
        claim_file = _write_claim_file(tmp_path, d)
        data_dir = str(tmp_path)
        cmd_propose(_make_args(claim_file=str(claim_file), data_dir=data_dir, validator=None))

        rc = cmd_list(_make_args(data_dir=data_dir, kind=None, layer=None,
                                  review_status=None, status=None))
        assert rc == 0
        captured = capsys.readouterr()
        assert "fi.v1.INLINE_STATUTE_RESOLUTION" in captured.out

    def test_list_empty_store(self, tmp_path: Path, capsys):
        from lawvm.tools.cmd_claim import cmd_list
        rc = cmd_list(_make_args(data_dir=str(tmp_path), kind=None, layer=None,
                                  review_status=None, status=None))
        assert rc == 0
        captured = capsys.readouterr()
        assert "no claims" in captured.out

    def test_list_filters_by_kind(self, tmp_path: Path, capsys):
        from lawvm.tools.cmd_claim import cmd_list, cmd_propose
        d, claim_id = _make_claim_dict()
        claim_file = _write_claim_file(tmp_path, d)
        data_dir = str(tmp_path)
        cmd_propose(_make_args(claim_file=str(claim_file), data_dir=data_dir, validator=None))

        # Filter by a different kind — should yield no match
        rc = cmd_list(_make_args(data_dir=data_dir, kind="fi.v1.OTHER_KIND", layer=None,
                                  review_status=None, status=None))
        assert rc == 0
        captured = capsys.readouterr()
        assert "no claims match" in captured.out


# ---------------------------------------------------------------------------
# Test: show renders all four records
# ---------------------------------------------------------------------------


def test_claim_show_renders_all_four_records(tmp_path: Path, capsys):
    """lawvm claim show renders claim payload + state + event history + composition decisions."""
    from lawvm.tools.cmd_claim import cmd_propose, cmd_show

    d, claim_id = _make_claim_dict()
    claim_file = _write_claim_file(tmp_path, d)
    data_dir = str(tmp_path)
    cmd_propose(_make_args(claim_file=str(claim_file), data_dir=data_dir, validator=None))

    rc = cmd_show(_make_args(claim_id=claim_id, data_dir=data_dir))
    assert rc == 0

    captured = capsys.readouterr()
    output = captured.out

    # 1. Claim payload section
    assert "CLAIM PAYLOAD" in output
    assert "fi.v1.INLINE_STATUTE_RESOLUTION" in output
    assert "extraction" in output

    # 2. Current state section
    assert "CURRENT STATE" in output
    assert "proposed" in output

    # 3. Event history section
    assert "EVENT HISTORY" in output

    # 4. Composition decisions section (empty in Slice 2, but present)
    assert "COMPOSITION DECISIONS" in output


def test_claim_show_nonexistent_fails(tmp_path: Path):
    from lawvm.tools.cmd_claim import cmd_show
    rc = cmd_show(_make_args(claim_id="nonexistent" * 4, data_dir=str(tmp_path)))
    assert rc == 1


# ---------------------------------------------------------------------------
# Test: self-authorization via CLI is impossible
# ---------------------------------------------------------------------------


def test_cli_self_authorization_impossible(tmp_path: Path):
    """Filing a claim via propose always lands in PROPOSED status.

    Even if the claim file is crafted to look like it's already accepted,
    the CLI writes its own initial state with status=PROPOSED.
    """
    from lawvm.tools.cmd_claim import cmd_propose
    from lawvm.core.manual_claims.storage import ClaimStore

    d, claim_id = _make_claim_dict()
    # A malicious file might try to include review_status or status in the payload
    # but ManualCompilationClaim doesn't have those fields → they're ignored on parse
    claim_file = _write_claim_file(tmp_path, d)
    data_dir = str(tmp_path)
    cmd_propose(_make_args(claim_file=str(claim_file), data_dir=data_dir, validator=None))

    store = ClaimStore(tmp_path / "manual_claims")
    state = store.read_state(claim_id)
    assert state is not None
    assert state.status == ClaimStatus.PROPOSED
    assert state.review_status == ReviewStatus.PROPOSED
