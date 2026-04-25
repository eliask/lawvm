from __future__ import annotations

from lawvm.tools import gold


def test_cmd_verify_replays_quietly(monkeypatch, tmp_path, capsys) -> None:
    manifest_path = tmp_path / "manifest.json"
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    gold_file = gold_dir / "2000_1.json"
    gold_file.write_text(
        '{"verified_date":"2026-03-21","provisions":{"section:1":{"text":"x","tier":2}}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(gold, "_load_manifest", lambda: {"statutes": [{"statute_id": "2000/1", "file": "2000_1.json"}]})
    monkeypatch.setattr(gold, "_get_statutes_list", lambda manifest: list(manifest["statutes"]))
    monkeypatch.setattr(gold, "_gold_dir", lambda: gold_dir)
    monkeypatch.setattr(gold, "_normalize_address", lambda address: address)
    monkeypatch.setattr(gold, "_clean_text", lambda text: text)

    seen: dict[str, object] = {}

    class DummySection:
        pass

    class DummyMaster:
        ir = object()

    def fake_replay_xml(sid: str, mode: str, **kwargs):
        seen["sid"] = sid
        seen["mode"] = mode
        seen.update(kwargs)
        return DummyMaster()

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.diff._extract_sections_ir", lambda _ir: {"section:1": DummySection()})
    monkeypatch.setattr("lawvm.core.ir_helpers.irnode_to_text", lambda _node: "x")

    gold._cmd_verify("2000/1", "legal_pit")
    out = capsys.readouterr().out

    assert "Verifying 2000/1 against gold" in out
    assert seen["sid"] == "2000/1"
    assert seen["mode"] == "legal_pit"
    assert seen["quiet"] is True


def test_cmd_verify_suppresses_raw_replay_chatter_for_1992_1612(capsys) -> None:
    gold._cmd_verify("1992/1612", "legal_pit")
    out = capsys.readouterr().out

    assert "Verifying 1992/1612 against gold" in out
    assert "Master 1992/1612 rehydrated." not in out
    assert "Applying 2 muutoslait..." not in out
    assert "WARNING source pathology:" not in out
