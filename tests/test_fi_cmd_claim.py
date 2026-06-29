"""Tests for lawvm claim CLI subcommands (v3 graph-native).

Covers:
  - CLI smoke tests for each subcommand
  - test_cli_self_authorization_impossible
  - test_claim_show_renders_graph_sections
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path


importlib.import_module("lawvm.finland.claim_kinds")



def _make_assertion_dict(statute_id: str = "711/2022", resolved: str = "1234/2020") -> dict:
    return {
        "kind": "fi.v1.INLINE_STATUTE_RESOLUTION",
        "layer": "extraction",
        "jurisdiction": "fi",
        "schema_version": "v1",
        "scope": {"statute_id": statute_id, "provision_ref": "section:3"},
        "target": {"statute_id": statute_id, "mention_span": "100-120"},
        "value": {"resolved_statute_id": resolved, "citation_form": f"lain {resolved}"},
        "valid_at": {"start": "2022-01-01", "end": None},
        "source_refs": [
            {
                "artifact_digest": "a" * 64,
                "structural_locator": "section:3",
                "bounded_quote_hash": "b" * 64,
                "normalization_policy_id": "v1",
                "byte_range": [0, 100],
            }
        ],
        "dependency_refs": [],
        "supersedes": [],
        "disputes": [],
        "rationale": "CLI test claim",
    }


def _write_claim_file(tmp_path: Path, d: dict) -> Path:
    p = tmp_path / "claim.json"
    p.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return p


def _make_args(**kwargs):
    class _Args:
        pass
    a = _Args()
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


def _graph_root(tmp_path: Path) -> str:
    return str(tmp_path / "provenance_graph")


def _load_assertions(tmp_path: Path) -> list[dict]:
    obj_dir = tmp_path / "provenance_graph" / "objects" / "sha256"
    if not obj_dir.exists():
        return []
    return [
        json.loads(f.read_text())
        for f in sorted(obj_dir.glob("*.json"))
        if "assertion_id" in json.loads(f.read_text()) and "kind" in json.loads(f.read_text())
    ]


def _propose_and_get_id(tmp_path: Path, d: dict | None = None) -> str:
    from lawvm.tools.cmd_claim import cmd_propose
    if d is None:
        d = _make_assertion_dict()
    cf = _write_claim_file(tmp_path, d)
    rc = cmd_propose(_make_args(claim_file=str(cf), graph_store_root=_graph_root(tmp_path)))
    assert rc == 0
    assertions = _load_assertions(tmp_path)
    return assertions[-1]["assertion_id"]


class TestCmdClaim:

    def test_propose_creates_assertion(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_propose
        d = _make_assertion_dict()
        cf = _write_claim_file(tmp_path, d)
        rc = cmd_propose(_make_args(claim_file=str(cf), graph_store_root=_graph_root(tmp_path)))
        assert rc == 0
        assertions = _load_assertions(tmp_path)
        assert len(assertions) == 1
        assert assertions[0]["kind"] == "fi.v1.INLINE_STATUTE_RESOLUTION"

    def test_propose_idempotent(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_propose
        d = _make_assertion_dict()
        cf = _write_claim_file(tmp_path, d)
        rc1 = cmd_propose(_make_args(claim_file=str(cf), graph_store_root=_graph_root(tmp_path)))
        rc2 = cmd_propose(_make_args(claim_file=str(cf), graph_store_root=_graph_root(tmp_path)))
        assert rc1 == 0
        assert rc2 == 0
        assertions = _load_assertions(tmp_path)
        assert len(assertions) == 1

    def test_accept_transitions_state(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_accept
        assertion_id = _propose_and_get_id(tmp_path)
        rc = cmd_accept(_make_args(assertion_id=assertion_id, graph_store_root=_graph_root(tmp_path)))
        assert rc == 0

        obj_dir = tmp_path / "provenance_graph" / "objects" / "sha256"
        reviewed = [
            json.loads(f.read_text())
            for f in obj_dir.glob("*.json")
            if json.loads(f.read_text()).get("attestation_kind") == "reviewed"
        ]
        assert len(reviewed) == 1
        assert reviewed[0]["payload"]["accepted"] is True

    def test_accept_nonexistent_claim_fails(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_accept
        rc = cmd_accept(_make_args(assertion_id="nonexistent" * 4, graph_store_root=_graph_root(tmp_path)))
        assert rc == 1

    def test_reject_transitions_state(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_reject
        assertion_id = _propose_and_get_id(tmp_path)
        rc = cmd_reject(_make_args(
            assertion_id=assertion_id,
            reason="wrong target",
            graph_store_root=_graph_root(tmp_path),
        ))
        assert rc == 0

        obj_dir = tmp_path / "provenance_graph" / "objects" / "sha256"
        reviewed = [
            json.loads(f.read_text())
            for f in obj_dir.glob("*.json")
            if json.loads(f.read_text()).get("attestation_kind") == "reviewed"
        ]
        assert any(not r["payload"].get("accepted") for r in reviewed)

    def test_retract_accepted_claim(self, tmp_path: Path):
        from lawvm.tools.cmd_claim import cmd_accept, cmd_retract
        assertion_id = _propose_and_get_id(tmp_path)
        cmd_accept(_make_args(assertion_id=assertion_id, graph_store_root=_graph_root(tmp_path)))
        rc = cmd_retract(_make_args(
            assertion_id=assertion_id,
            reason="bad claim",
            graph_store_root=_graph_root(tmp_path),
        ))
        assert rc == 0

        obj_dir = tmp_path / "provenance_graph" / "objects" / "sha256"
        retracted = [
            json.loads(f.read_text())
            for f in obj_dir.glob("*.json")
            if json.loads(f.read_text()).get("attestation_kind") == "retracted"
        ]
        assert len(retracted) == 1

    def test_list_returns_claims(self, tmp_path: Path, capsys):
        from lawvm.tools.cmd_claim import cmd_list
        _propose_and_get_id(tmp_path)
        rc = cmd_list(_make_args(
            kind=None, layer=None, has_attestation_kind=None,
            graph_store_root=_graph_root(tmp_path),
        ))
        assert rc == 0
        out = capsys.readouterr().out
        assert "fi.v1.INLINE_STATUTE_RESOLUTION" in out

    def test_list_empty_store(self, tmp_path: Path, capsys):
        from lawvm.tools.cmd_claim import cmd_list
        rc = cmd_list(_make_args(
            kind=None, layer=None, has_attestation_kind=None,
            graph_store_root=_graph_root(tmp_path),
        ))
        assert rc == 0
        out = capsys.readouterr().out
        assert "no assertions" in out

    def test_list_filters_by_kind(self, tmp_path: Path, capsys):
        from lawvm.tools.cmd_claim import cmd_list
        _propose_and_get_id(tmp_path)
        rc = cmd_list(_make_args(
            kind="fi.v1.OTHER_KIND", layer=None, has_attestation_kind=None,
            graph_store_root=_graph_root(tmp_path),
        ))
        assert rc == 0
        out = capsys.readouterr().out
        assert "no assertions match" in out


def test_claim_list_filters_by_status_and_review_status(tmp_path: Path, capsys):
    """claim list applies graph-native status filters exposed by argparse."""
    from lawvm.tools.cmd_claim import cmd_accept, cmd_list
    accepted_id = _propose_and_get_id(tmp_path)
    cmd_accept(_make_args(
        assertion_id=accepted_id,
        graph_store_root=_graph_root(tmp_path),
    ))

    rc = cmd_list(_make_args(
        kind=None,
        layer=None,
        status="accepted",
        review_status="verified_manual",
        has_attestation_kind=None,
        graph_store_root=_graph_root(tmp_path),
    ))

    assert rc == 0
    out = capsys.readouterr().out
    assert "accepted" in out
    assert "verified_manual" in out
    assert "fi.v1.INLINE_STATUTE_RESOLUTION" in out

    rc = cmd_list(_make_args(
        kind=None,
        layer=None,
        status="rejected",
        review_status=None,
        has_attestation_kind=None,
        graph_store_root=_graph_root(tmp_path),
    ))
    assert rc == 0
    assert "no assertions match" in capsys.readouterr().out


def test_claim_list_parser_accepts_graph_filters() -> None:
    """Argparse exposes the graph-native filters consumed by cmd_list."""
    from lawvm.tools.cli import _build_parser

    args = _build_parser().parse_args([
        "claim",
        "list",
        "--status",
        "accepted",
        "--review-status",
        "verified_manual",
        "--has-attestation-kind",
        "reviewed",
    ])

    assert args.command == "claim"
    assert args.claim_subcommand == "list"
    assert args.status == "accepted"
    assert args.review_status == "verified_manual"
    assert args.has_attestation_kind == "reviewed"


def test_claim_show_renders_all_four_records(tmp_path: Path, capsys):
    """lawvm claim show renders assertion payload + attestations + authorization + source provenance."""
    from lawvm.tools.cmd_claim import cmd_show
    assertion_id = _propose_and_get_id(tmp_path)

    rc = cmd_show(_make_args(assertion_id=assertion_id, graph_store_root=_graph_root(tmp_path)))
    assert rc == 0

    out = capsys.readouterr().out
    assert "ASSERTION PAYLOAD" in out
    assert "fi.v1.INLINE_STATUTE_RESOLUTION" in out
    assert "ATTESTATIONS" in out
    assert "AUTHORIZATION RESULT" in out
    assert "SOURCE PROVENANCE" in out


def test_claim_show_uses_requested_profile(tmp_path: Path, capsys):
    """lawvm claim show --profile selects the authorization read-model profile."""
    from lawvm.tools.cmd_claim import cmd_show
    assertion_id = _propose_and_get_id(tmp_path)

    rc = cmd_show(_make_args(
        assertion_id=assertion_id,
        graph_store_root=_graph_root(tmp_path),
        profile="fi_strict",
    ))
    assert rc == 0

    out = capsys.readouterr().out
    assert "profile_name:         fi_strict" in out


def test_claim_show_parser_accepts_profile() -> None:
    """Argparse exposes claim show --profile with the command helper's destination."""
    from lawvm.tools.cli import _build_parser

    args = _build_parser().parse_args([
        "claim",
        "show",
        "a" * 64,
        "--profile",
        "fi_strict",
    ])

    assert args.command == "claim"
    assert args.claim_subcommand == "show"
    assert args.claim_id == "a" * 64
    assert args.profile == "fi_strict"


def test_claim_show_rejects_unknown_profile(tmp_path: Path, capsys):
    """Direct command calls fail clearly on unsupported profiles."""
    from lawvm.tools.cmd_claim import cmd_show
    assertion_id = _propose_and_get_id(tmp_path)

    rc = cmd_show(_make_args(
        assertion_id=assertion_id,
        graph_store_root=_graph_root(tmp_path),
        profile="unknown_profile",
    ))

    assert rc == 1
    assert "unsupported claim show profile" in capsys.readouterr().err


def test_claim_show_nonexistent_fails(tmp_path: Path):
    from lawvm.tools.cmd_claim import cmd_show
    rc = cmd_show(_make_args(assertion_id="nonexistent" * 4, graph_store_root=_graph_root(tmp_path)))
    assert rc == 1


def test_cli_self_authorization_impossible(tmp_path: Path):
    """Filing an assertion always results in claim_submitted only, never pre-accepted."""
    from lawvm.tools.cmd_claim import cmd_propose
    d = _make_assertion_dict()
    cf = _write_claim_file(tmp_path, d)
    rc = cmd_propose(_make_args(claim_file=str(cf), graph_store_root=_graph_root(tmp_path)))
    assert rc == 0

    obj_dir = tmp_path / "provenance_graph" / "objects" / "sha256"
    attestations = [
        json.loads(f.read_text())
        for f in obj_dir.glob("*.json")
        if "attestation_kind" in json.loads(f.read_text())
    ]
    kinds = {a["attestation_kind"] for a in attestations}
    assert "claim_submitted" in kinds
    assert "reviewed" not in kinds
