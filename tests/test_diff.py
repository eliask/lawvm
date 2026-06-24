from __future__ import annotations

from types import SimpleNamespace

from lxml import etree

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools.cli import _build_parser
from lawvm.tools.diff import _diff_sections_ir_vs_xml, _diff_sync, _print_compile_summary


def test_diff_strict_help_describes_quirks_mode_correctly(capsys) -> None:
    parser = _build_parser()

    try:
        parser.parse_args(["diff", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    out = " ".join(capsys.readouterr().out.split())
    assert "where recoveries can proceed with evidence" in out
    assert "quirks mode where heuristics are blocked" not in out


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


def test_diff_compile_summary_accepts_report_record_dict(capsys) -> None:
    _print_compile_summary(
        report_record={
            "canonical_ops": (SimpleNamespace(op_id="replace_1"),),
            "failed_ops": (),
            "projection_rows": (
                {
                    "kind": "ELAB.SOURCE_PATHOLOGY",
                    "detail": {"code": "DESTRUCTIVE_SHAPE_LOSS_RISK"},
                },
            ),
            "source_pathologies": (
                {"code": "DESTRUCTIVE_SHAPE_LOSS_RISK"},
            ),
            "strict_fail_reasons": (),
        },
    )

    out = capsys.readouterr().out
    assert "Compile summary: strict=YES  canonical=1  failed=0  projection_rows=1" in out
    assert "Projection rows: ELAB.SOURCE_PATHOLOGY" in out
    assert "Pathologies  : DESTRUCTIVE_SHAPE_LOSS_RISK" in out


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


def test_diff_sync_compares_materialized_pit_ir(monkeypatch, capsys) -> None:
    fold_ir = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.CONTENT, text="expired"),))
    materialized_ir = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.CONTENT, text="live"),))
    captured: dict[str, IRNode] = {}

    def fake_replay_xml(statute_id: str, **kwargs):
        return SimpleNamespace(
            title="PIT replay",
            ir=fold_ir,
            materialized_state=SimpleNamespace(ir=materialized_ir),
            products=SimpleNamespace(temporal_events=()),
        )

    def fake_diff_sections(replay_ir, oracle_root, address_filter, threshold, show_all, *, show_text=False):
        captured["replay_ir"] = replay_ir

    monkeypatch.setattr("lawvm.tools.diff.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.diff._diff_sections_ir_vs_xml", fake_diff_sections)
    monkeypatch.setattr(
        "lawvm.tools.diff.get_consolidated_oracle_context",
        lambda sid, selector: SimpleNamespace(locator="fake://oracle"),
    )
    monkeypatch.setattr(
        "lawvm.tools.diff.get_corpus",
        lambda: SimpleNamespace(read_locator=lambda locator: b"<act><body/></act>"),
    )

    _diff_sync(
        sid="1991/1",
        address_filter=None,
        threshold=1.0,
        show_all=False,
        mode="official_consolidation",
    )

    assert captured["replay_ir"] is materialized_ir
