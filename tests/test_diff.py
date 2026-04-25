from __future__ import annotations

from types import SimpleNamespace

from lxml import etree

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools.diff import _diff_sections_ir_vs_xml, _diff_sync


def test_diff_sections_treats_temporary_oracle_stub_as_editorial(capsys) -> None:
    replay_root = IRNode(kind=IRNodeKind.BODY, children=())
    oracle_root = etree.fromstring(
        """
        <act>
          <body>
            <section eId="sec_21b">
              <num>21 b §</num>
              <content>
                <p>21 b § oli väliaikaisesti voimassa 24.11.2021–30.1.2022 L 984/2021.</p>
              </content>
            </section>
          </body>
        </act>
        """
    )

    _diff_sections_ir_vs_xml(replay_root, oracle_root, None, threshold=0.95, show_all=True)

    out = capsys.readouterr().out
    assert "editorial (stub)" in out
    assert "MISSING" not in out


def test_diff_sync_replays_quietly(monkeypatch, capsys) -> None:
    called: dict[str, object] = {}

    def fake_replay_xml(statute_id: str, **kwargs):
        called["statute_id"] = statute_id
        called["quiet"] = kwargs.get("quiet")
        return SimpleNamespace(
            title="Quiet replay",
            ir=IRNode(kind=IRNodeKind.BODY, children=()),
            products=SimpleNamespace(temporal_events=()),
        )

    monkeypatch.setattr("lawvm.tools.diff.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.tools.diff.get_consolidated_oracle_context",
        lambda sid, selector: SimpleNamespace(locator="fake://oracle"),
    )
    monkeypatch.setattr(
        "lawvm.tools.diff.get_corpus",
        lambda: SimpleNamespace(
            read_locator=lambda locator: b"<act><body/></act>",
        ),
    )

    _diff_sync(
        sid="1991/1",
        address_filter=None,
        threshold=1.0,
        show_all=False,
        mode="legal_pit",
    )

    assert called == {"statute_id": "1991/1", "quiet": True}
    out = capsys.readouterr().out
    assert "Statute : 1991/1" in out
