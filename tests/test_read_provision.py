"""Tests for the read verb: selector routing, render, JSON passthrough."""

from __future__ import annotations

import json
from typing import Any

from lawvm.tools import read_provision as rp


class _Args:
    def __init__(self, **kwargs: Any) -> None:
        self.statute_id = ""
        self.selector = ""
        self.as_of = "2026-06-09"
        self.query_type = "in_force"
        self.territory = None
        self.include_ir = False
        self.raw = False
        self.xml = False
        self.json = False
        self.temporal_labels = False
        self.subsections = False
        self.jurisdiction = "fi"
        for k, v in kwargs.items():
            setattr(self, k, v)


_PAYLOAD = {
    "schema": "lawvm.provision_state.v1",
    "statute_id": "2011/805",
    "title": "Esitutkintalaki",
    "status": "selected",
    "query": {"provision": "chapter:3/section:1", "as_of": "2026-06-09",
              "query_type": "in_force"},
    "version": {"effective": "2026-04-14", "content_state": "live"},
    "source": {"statute_id": "2026/222"},
    "text": {"rendered": "1 § some in-force text", "available": True},
}


def _patch_replay(monkeypatch, payload=_PAYLOAD):
    captured = {}

    def fake_resolve(**kwargs):
        captured.update(kwargs)
        return payload

    monkeypatch.setattr("lawvm.provision_state.resolve_provision_state", fake_resolve)
    return captured


class TestSelectorRouting:
    def test_section_selector_lowered_to_locator(self, monkeypatch):
        captured = _patch_replay(monkeypatch)
        rp.main(_Args(statute_id="2011/805", selector="§3:1"))
        assert captured["provision"] == "chapter:3/section:1"

    def test_momentti_selector_lowered(self, monkeypatch):
        captured = _patch_replay(monkeypatch)
        rp.main(_Args(statute_id="2011/805", selector="§3:1.2"))
        assert captured["provision"] == "chapter:3/section:1/subsection:2"

    def test_legacy_locator_passthrough(self, monkeypatch):
        captured = _patch_replay(monkeypatch)
        rp.main(_Args(statute_id="2011/805", selector="chapter:3/section:1"))
        assert captured["provision"] == "chapter:3/section:1"

    def test_lettered_section_selectors_lower_to_compact_locator(self, monkeypatch):
        for selector in ("§2d", "§2 d", "2 d §", "section:2 d"):
            captured = _patch_replay(monkeypatch)
            rp.main(_Args(statute_id="2021/728", selector=selector))
            assert captured["provision"] == "section:2d"


class TestHumanRender:
    def test_render_uses_typed_selector(self, monkeypatch, capsys):
        _patch_replay(monkeypatch)
        rp.main(_Args(statute_id="2011/805", selector="§3:1"))
        out = capsys.readouterr().out
        assert "2011/805 §3:1" in out  # typed selector, not lowered locator
        assert "in-force text" in out
        assert "eff 2026-04-14" in out
        assert "src 2026/222" in out

    def test_tombstone_render(self, monkeypatch, capsys):
        payload = dict(_PAYLOAD)
        payload["version"] = {"effective": "2026-04-14", "content_state": "tombstone"}
        payload["text"] = {"rendered": "", "available": False}
        _patch_replay(monkeypatch, payload)
        rp.main(_Args(statute_id="2011/805", selector="§3:1"))
        out = capsys.readouterr().out
        assert "tombstone" in out

    def test_unresolved_render(self, monkeypatch, capsys):
        payload = {
            "statute_id": "2011/805", "title": "", "status": "address_not_found",
            "query": {"provision": "chapter:9/section:9", "as_of": "2026-06-09",
                      "query_type": "in_force"},
            "address_candidates": [],
        }
        _patch_replay(monkeypatch, payload)
        rp.main(_Args(statute_id="2011/805", selector="§9:9"))
        out = capsys.readouterr().out
        assert "address_not_found" in out


class TestJsonPassthrough:
    def test_json_is_provision_state_pin(self, monkeypatch, capsys):
        _patch_replay(monkeypatch)
        rp.main(_Args(statute_id="2011/805", selector="§3:1", json=True))
        out = capsys.readouterr().out
        parsed = json.loads(out)
        # display_selector must NOT leak into the JSON pin (MeVM contract).
        assert "display_selector" not in parsed
        assert parsed["schema"] == "lawvm.provision_state.v1"
        assert parsed["text"]["rendered"] == "1 § some in-force text"
